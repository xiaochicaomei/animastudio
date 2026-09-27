"""Bootstrap a LoRA dataset from images we own.

The style-training loop needs a dataset. Instead of scraping third-party art,
this generates one with the studio's own model: one original character, many
poses / scenes / expressions. Fully self-owned, so the resulting LoRA is clean
for commercial use.

The character sheet is supplied by the caller (see --sheet) and repeated
verbatim in every prompt, which is what makes the set learnable.

CLI:
  python make_style_dataset.py --name orig_student --count 24 --sheet sheet.txt
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402  (reuse build_workflow + Comfy)
from comfy import Comfy  # noqa: E402

ROOT = Path(r"D:\AnimaStudio")
RAW_OUT = ROOT / "workspace" / "out" / "raw"

POSES = [
    "standing, arms at sides", "standing, one hand on hip", "walking, from side",
    "sitting on chair, hands on lap", "sitting on floor, knees up",
    "crouching, looking down", "leaning against wall, arms crossed",
    "reaching out one hand", "holding a book with both hands",
    "turning to look back over shoulder", "jumping mid air",
    "lying on sofa, head on cushion",
]
SCENES = [
    "school classroom, desks, window light", "school hallway, lockers",
    "school rooftop, railing, sky", "library, tall bookshelves",
    "cafe table, cup on table", "bedroom, curtains, soft lamp light",
    "park at sunset, trees, bench", "train station platform, evening",
    "shrine steps, torii, autumn leaves", "beach, sea in background",
    "city street at night, neon signs", "art room, easel and paints",
]
EXPRESSIONS = [
    "neutral expression", "gentle smile", "open mouth smile",
    "surprised expression", "serious expression", "blushing",
    "looking away, shy", "determined expression",
]
SHOTS = [
    "upper body, portrait", "full body, wide shot", "close up on face",
    "from below, low angle", "from above, high angle", "cowboy shot",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="dataset / character id")
    ap.add_argument("--sheet", required=True, help="text file with the character sheet tags")
    ap.add_argument("--count", type=int, default=24)
    ap.add_argument("--width", type=int, default=832)
    ap.add_argument("--height", type=int, default=1216)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--cfg", type=float, default=4.0)
    ap.add_argument("--seed-base", type=int, default=700000)
    args = ap.parse_args()

    sheet = Path(args.sheet).read_text(encoding="utf-8").strip().replace("\n", ", ")
    out_dir = ROOT / "workspace" / "refs" / args.name
    out_dir.mkdir(parents=True, exist_ok=True)

    c = Comfy()
    if not c.ready():
        print("ComfyUI not reachable - start it first")
        return 2

    made = 0
    t_all = time.time()
    for i in range(args.count):
        pose = POSES[i % len(POSES)]
        scene = SCENES[(i * 5 + 1) % len(SCENES)]
        expr = EXPRESSIONS[(i * 3 + 2) % len(EXPRESSIONS)]
        shot = SHOTS[(i * 7 + 3) % len(SHOTS)]
        job = {
            "id": "ref%03d" % i,
            "width": args.width,
            "height": args.height,
            "seed": args.seed_base + i,
            "turbo": False,
            "steps": args.steps,
            "cfg": args.cfg,
            "prompt": "%s, %s, %s, %s, %s, solo" % (pose, scene, expr, shot, sheet),
            "prefix": "ref_%s_%03d" % (args.name, i),
        }
        wf = runner.build_workflow(job)
        t0 = time.time()
        try:
            pid = c.submit(wf)
            entry, _ = c.wait(pid)
            if c.status_str(entry) != "success":
                print("  %03d FAILED %s" % (i, c.error_messages(entry)))
                continue
            imgs = c.output_images(entry)
            if not imgs:
                print("  %03d no output" % i)
                continue
            src = RAW_OUT / imgs[0].get("subfolder", "") / imgs[0]["filename"]
            dst = out_dir / ("%03d.png" % i)
            dst.write_bytes(src.read_bytes())
            made += 1
            print("  %03d %-42s %5.1fs" % (i, pose[:40], time.time() - t0), flush=True)
        except Exception as e:
            print("  %03d ERROR %s" % (i, e))

    meta = {
        "name": args.name,
        "sheet": sheet,
        "count": made,
        "width": args.width,
        "height": args.height,
        "steps": args.steps,
        "cfg": args.cfg,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "self-generated with Anima (self-owned dataset)",
    }
    (out_dir / "_sheet.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("dataset ready: %d images -> %s (%.1fs total)" % (made, out_dir, time.time() - t_all))
    return 0


if __name__ == "__main__":
    sys.exit(main())