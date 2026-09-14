"""Turn the Playwright capture video into docs/media/demo.gif.

    npx playwright test -c playwright.capture.config.ts   (in dashboard/)
    uv run python scripts/make_demo_gif.py

Uses Playwright's bundled ffmpeg (PNG frames only; it has no GIF muxer) and Pillow.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FPS = 6
WIDTH = 820


def find_ffmpeg() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if sys.platform != "win32":
        base = Path.home() / ".cache" / "ms-playwright"
    hits = sorted(base.glob("ffmpeg-*/ffmpeg*"))
    if not hits:
        raise SystemExit("Playwright ffmpeg not found; run `npx playwright install chromium`")
    return hits[-1]


def main() -> None:
    videos = sorted((ROOT / "dashboard" / "capture-out").rglob("*.webm"))
    if not videos:
        raise SystemExit("no capture video; run the capture config first")
    out = ROOT / "docs" / "media" / "demo.gif"
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [
                str(find_ffmpeg()),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(videos[-1]),
                "-r",
                str(FPS),  # this ffmpeg build has no `fps` filter; -r resamples the output
                "-vf",
                f"scale={WIDTH}:-1",
                f"{tmp}/f%04d.png",
            ],
            check=True,
        )
        frames = [Image.open(p).convert("RGB") for p in sorted(Path(tmp).glob("f*.png"))]
        # drop the leading blank frames Playwright records before first paint
        while frames and frames[0].getextrema() == ((0, 0), (0, 0), (0, 0)):
            frames.pop(0)
        quant = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for f in frames]
        quant[0].save(
            out,
            save_all=True,
            append_images=quant[1:],
            duration=int(1000 / FPS),
            loop=0,
            optimize=True,
        )
    print(f"{out} ({out.stat().st_size / 1e6:.1f} MB, {len(frames)} frames)")


if __name__ == "__main__":
    main()
