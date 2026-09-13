"""Export the merged triage model for serving (SPEC §7.4).

uv run --with "torch transformers optimum onnx" python training/export.py --model .sentinel/models/triage-lora/merged --onnx
# serve with vLLM and register in LiteLLM:
#   vllm serve .sentinel/models/triage-lora/merged --served-model-name sentinel-triage --port 8001
#   SENTINEL_TRIAGE_ENDPOINT=http://localhost:8001/v1 SENTINEL_TRIAGE_MODEL=openai/sentinel-triage
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=".sentinel/models/triage-lora/merged")
    ap.add_argument("--onnx", action="store_true", help="also export ONNX for CPU inference")
    ap.add_argument("--out", default=".sentinel/models/triage-export")
    a = ap.parse_args(argv)
    src = Path(a.model)
    if not src.exists():
        print(f"model not found: {src}", file=sys.stderr)
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.onnx:
        from optimum.onnxruntime import ORTModelForSequenceClassification
        from transformers import AutoTokenizer

        ORTModelForSequenceClassification.from_pretrained(src, export=True).save_pretrained(
            out / "onnx"
        )
        AutoTokenizer.from_pretrained(src).save_pretrained(out / "onnx")
        print(f"ONNX model written to {out / 'onnx'}")
    (out / "SERVE.md").write_text(
        f"# Serving sentinel-triage\n\n```bash\nvllm serve {src} --served-model-name sentinel-triage --port 8001\n```\n"
        "Then set `SENTINEL_TRIAGE_ENDPOINT=http://localhost:8001/v1` and `SENTINEL_TRIAGE_MODEL=openai/sentinel-triage`.\n",
        encoding="utf-8",
    )
    print(f"wrote {out / 'SERVE.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
