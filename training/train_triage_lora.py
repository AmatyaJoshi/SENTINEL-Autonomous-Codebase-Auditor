"""LoRA fine-tune of Qwen2.5-Coder for the triage classifier (SPEC §7.3).

Sequence classification head (last-token pooling), BCE with positive-class weighting, LoRA r=16 /
alpha=32 / dropout 0.05 on q,k,v,o + MLP, lr 2e-4, 3 epochs, effective batch 64, max_len 2048, bf16,
cosine schedule with 5% warmup. Logs to W&B if `WANDB_API_KEY` is set, otherwise to stdout/JSON.
Designed for one consumer GPU in < 2 h on ~3k rows.

    uv run --with "torch transformers peft datasets accelerate scikit-learn" \
        python training/train_triage_lora.py --config training/configs/qwen1.5b.yaml

Heavy deps are imported lazily so the rest of Sentinel never depends on them.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "base_model": "Qwen/Qwen2.5-Coder-1.5B",
    "data_dir": "training/data",
    "output_dir": ".sentinel/models/triage-lora",
    "max_len": 2048,
    "epochs": 3,
    "lr": 2e-4,
    "batch_size": 16,
    "grad_accum": 4,
    "warmup_ratio": 0.05,
    "lora": {
        "r": 16,
        "alpha": 32,
        "dropout": 0.05,
        "target_modules": [
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
    },
    "bf16": True,
    "seed": 20260914,
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true", help="validate data/config without training")
    a = ap.parse_args(argv)
    cfg = dict(DEFAULT_CONFIG)
    if a.config:
        cfg.update(yaml.safe_load(Path(a.config).read_text(encoding="utf-8")) or {})
    data = Path(cfg["data_dir"])
    train_rows, val_rows = load_jsonl(data / "train.jsonl"), load_jsonl(data / "val.jsonl")
    if len(train_rows) < 100:
        print(
            f"only {len(train_rows)} training rows; SPEC targets ≥ 3,000. Refusing to train a meaningless model.",
            file=sys.stderr,
        )
        return 2
    pos = sum(r["label"] for r in train_rows)
    print(f"train={len(train_rows)} (pos={pos}) val={len(val_rows)} base={cfg['base_model']}")
    if a.dry_run:
        return 0

    import torch
    from datasets import Dataset
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    set_seed(int(cfg["seed"]))
    tok = AutoTokenizer.from_pretrained(cfg["base_model"])
    tok.pad_token = tok.pad_token or tok.eos_token
    tok.padding_side = "left"  # last-token pooling
    model = AutoModelForSequenceClassification.from_pretrained(
        cfg["base_model"],
        num_labels=1,
        torch_dtype=torch.bfloat16 if cfg["bf16"] else torch.float32,
    )
    model.config.pad_token_id = tok.pad_token_id
    lora = cfg["lora"]
    model = get_peft_model(
        model,
        LoraConfig(
            task_type=TaskType.SEQ_CLS,
            r=lora["r"],
            lora_alpha=lora["alpha"],
            lora_dropout=lora["dropout"],
            target_modules=lora["target_modules"],
        ),
    )
    model.print_trainable_parameters()

    def enc(batch: dict[str, list[Any]]) -> dict[str, Any]:
        out = tok(batch["text"], truncation=True, max_length=int(cfg["max_len"]))
        out["labels"] = [float(x) for x in batch["label"]]
        return out

    ds_train = Dataset.from_list(train_rows).map(
        enc, batched=True, remove_columns=[c for c in train_rows[0] if c != "label"]
    )
    ds_val = (
        Dataset.from_list(val_rows).map(
            enc, batched=True, remove_columns=[c for c in val_rows[0] if c != "label"]
        )
        if val_rows
        else None
    )
    pos_weight = torch.tensor([(len(train_rows) - pos) / max(pos, 1)])

    class WeightedTrainer(Trainer):  # type: ignore[misc]
        def compute_loss(
            self, model: Any, inputs: dict[str, Any], return_outputs: bool = False, **_: Any
        ) -> Any:
            labels = inputs.pop("labels")
            outputs = model(**inputs)
            loss = torch.nn.functional.binary_cross_entropy_with_logits(
                outputs.logits.squeeze(-1).float(),
                labels.float(),
                pos_weight=pos_weight.to(outputs.logits.device),
            )
            return (loss, outputs) if return_outputs else loss

    args = TrainingArguments(
        output_dir=cfg["output_dir"],
        num_train_epochs=float(cfg["epochs"]),
        learning_rate=float(cfg["lr"]),
        per_device_train_batch_size=int(cfg["batch_size"]),
        gradient_accumulation_steps=int(cfg["grad_accum"]),
        warmup_ratio=float(cfg["warmup_ratio"]),
        lr_scheduler_type="cosine",
        bf16=bool(cfg["bf16"]),
        logging_steps=10,
        eval_strategy="epoch" if ds_val else "no",
        save_strategy="epoch",
        save_total_limit=2,
        report_to=["wandb"] if __import__("os").environ.get("WANDB_API_KEY") else [],
        seed=int(cfg["seed"]),
    )
    trainer = WeightedTrainer(
        model=model, args=args, train_dataset=ds_train, eval_dataset=ds_val, tokenizer=tok
    )
    t0 = time.time()
    trainer.train()
    out = Path(cfg["output_dir"])
    model.save_pretrained(out / "adapter")
    merged = model.merge_and_unload()
    merged.save_pretrained(out / "merged")
    tok.save_pretrained(out / "merged")
    (out / "train_meta.json").write_text(
        json.dumps(
            {
                "config": cfg,
                "train_rows": len(train_rows),
                "positives": pos,
                "minutes": round((time.time() - t0) / 60, 1),
            },
            indent=2,
        )
    )
    print(f"saved adapter + merged model to {out} in {(time.time() - t0) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
