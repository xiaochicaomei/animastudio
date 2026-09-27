"""Verify a deployed style LoRA actually does something.

Two failure modes this catches:
  * the LoRA loads but nothing changes (key mismatch after conversion) - silent
  * the LoRA overpowers the prompt (strength too high)

Renders each probe twice with the SAME seed: once with the LoRA, once without,
then reports the pixel difference and writes a side-by-side comparison.

CLI:
  python verify_style.py --name orig_student [--strength 0.8]
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402
from comfy import Comfy  # noqa: E402

ROOT = Path(r"D:\AnimaStudio")
REG = ROOT / "workspace" / "jobs" / "styles.json"
OUT = ROOT / "workspace" / "out" / "verify"

PROMPTS = [
    ("v1", "{t}, 1girl, solo, upper body, classroom, window light, gentle smile"),
    ("v2", "{t}, 1girl, solo, full body, standing, school hallway, from side"),
    ("v3", "{t}, 1girl, solo, close up on face, surprised expression, outdoors, sunset"),
]


def render(c, job):
    wf = runner.build_workflow(job)
    pid = c.submit(wf)
    entry, _ = c.wait(pid)
    if c.status_str(entry) != "success":
        return None, c.error_messages(entry)
    imgs = c.output_images(entry)
    if not imgs:
        return None, "no output"
    src = runner.RAW_OUT / imgs[0].get("subfolder", "") / imgs[0]["filename"]
    return src, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--strength", type=float, default=None)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--width", type=int, default=832)
    ap.add_argument("--height", type=int, default=1216)
    args = ap.parse_args()

    reg = json.loads(REG.read_text(encoding="utf-8")).get(args.name)
    if not reg:
        print("style not registered: %s (run deploy first)" % args.name)
        return 2
    trigger = reg["trigger"]
    lora = reg["lora_file"]
    strength = args.strength if args.strength is not None else reg.get("strength", 0.8)
    outdir = OUT / args.name
    outdir.mkdir(parents=True, exist_ok=True)

    c = Comfy()
    if not c.ready():
        print("ComfyUI not reachable")
        return 2

    print("style=%s trigger=%s lora=%s strength=%s" % (args.name, trigger, lora, strength))
    rows = []
    for i, (pid, tpl) in enumerate(PROMPTS):
        prompt = tpl.format(t=trigger)
        base = {"id": pid, "width": args.width, "height": args.height, "seed": 4242 + i,
                "turbo": False, "steps": args.steps, "cfg": 4.0, "prompt": prompt,
                "prefix": "verify_%s_%s" % (args.name, pid)}
        job_on = dict(base, style_lora=lora, style_strength=strength)
        job_off = dict(base)
        p_on, err_on = render(c, job_on)
        p_off, err_off = render(c, job_off)
        if not p_on or not p_off:
            print("  %s render failed: %s %s" % (pid, err_on, err_off))
            continue
        a = np.asarray(Image.open(p_on).convert("RGB"), dtype=np.float32)
        b = np.asarray(Image.open(p_off).convert("RGB"), dtype=np.float32)
        diff = float(np.abs(a - b).mean())
        shutil.copy2(p_on, outdir / ("%s_with.png" % pid))
        shutil.copy2(p_off, outdir / ("%s_without.png" % pid))
        pair = Image.new("RGB", (a.shape[1] * 2 + 12, a.shape[0]), (20, 20, 20))
        pair.paste(Image.open(p_on).convert("RGB"), (0, 0))
        pair.paste(Image.open(p_off).convert("RGB"), (a.shape[1] + 12, 0))
        pair.save(outdir / ("%s_compare.png" % pid))
        rows.append((pid, diff))
        print("  %s: mean pixel diff = %.2f  %s" % (pid, diff,
              "OK" if diff > 6 else "WEAK - LoRA may not be applied!"))

    if rows:
        avg = sum(d for _, d in rows) / len(rows)
        verdict = "LoRA is active" if avg > 6 else "LoRA looks INACTIVE - check the key conversion"
        print("average diff %.2f -> %s" % (avg, verdict))
        print("comparisons: %s" % outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())