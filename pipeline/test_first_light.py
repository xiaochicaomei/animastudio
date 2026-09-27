"""AnimaStudio - first light test.

Generates one image through the ComfyUI API and reports timing + VRAM usage.
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from comfy import Comfy, ComfyError  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--cfg", type=float, default=4.0)
    ap.add_argument("--seed", type=int, default=755918130909406)
    ap.add_argument("--turbo", action="store_true")
    ap.add_argument("--prefix", default="anima_test")
    args = ap.parse_args()

    wf = json.loads((HERE / "templates" / "anima_t2i.json").read_text(encoding="utf-8"))

    wf["30"]["inputs"].update({"width": args.width, "height": args.height})
    wf["40"]["inputs"].update({"seed": args.seed, "steps": args.steps, "cfg": args.cfg})
    wf["60"]["inputs"]["filename_prefix"] = args.prefix
    if args.prompt:
        wf["20"]["inputs"]["text"] = args.prompt
    if args.turbo:
        wf["40"]["inputs"]["model"] = ["10", 0]
        wf["40"]["inputs"].update({"steps": 10, "cfg": 1.0, "sampler_name": "euler"})

    c = Comfy()
    if not c.ready():
        print("ERROR: ComfyUI not reachable at " + c.base)
        return 2

    free0, total = c.vram_free()
    print("VRAM before: free=%.2fGB / total=%.2fGB" % (free0 / 2 ** 30, total / 2 ** 30))
    print(
        "submitting: %dx%d steps=%s cfg=%s turbo=%s"
        % (args.width, args.height, wf["40"]["inputs"]["steps"], wf["40"]["inputs"]["cfg"], args.turbo)
    )

    t0 = time.time()
    try:
        pid = c.submit(wf)
    except ComfyError as e:
        print("SUBMIT FAILED:", e)
        return 3
    print("prompt_id:", pid)

    try:
        entry, min_free = c.wait(pid, on_tick=lambda el: print("  ... %5.1fs" % el))
    except ComfyError as e:
        print("WAIT FAILED:", e)
        return 3

    dt = time.time() - t0
    print("status: %s | elapsed: %.1fs" % (c.status_str(entry), dt))
    if min_free is not None:
        print(
            "VRAM peak usage approx: %.2fGB (min free %.2fGB)"
            % ((total - min_free) / 2 ** 30, min_free / 2 ** 30)
        )
    for m in c.error_messages(entry):
        print("ERROR MSG:", m)

    imgs = c.output_images(entry)
    for i in imgs:
        print("output image:", json.dumps(i, ensure_ascii=False))
    if not imgs:
        print("NO IMAGE PRODUCED")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())