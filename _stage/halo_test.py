"""Why does Anima-2.9B draw the character as a cut-out with a white halo?

The prompt contains no outline/border/sticker tag and the negative bans nothing of the
kind, so the halo is the model's own habit rather than something the pipeline asked for.
Renders the same seed twice - control, and with the halo named in the negative - so the
only difference is whether the term is present.

    python _stage/halo_test.py
"""
import json
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(r"D:\AnimaStudio")
sys.path.insert(0, str(ROOT / "pipeline"))
import runner  # noqa: E402
from comfy import Comfy  # noqa: E402

jobs = {j["id"]: j for j in (json.loads(l) for l in
                             (ROOT / "workspace" / "jobs" / "jobs.jsonl")
                             .read_text(encoding="utf-8").splitlines() if l.strip())}
ref = jobs.get("p001f") or list(jobs.values())[0]
prompt = ref["prompt"].replace("style_sodalord_style, ", "")
seed = ref.get("seed", 1)

HALO = "outline, white outline, border, sticker, cutout, die-cut"

c = Comfy()
made = []
for label, extra_neg in (("对照（无额外负向）", None), ("加 halo 负向", HALO)):
    job = {"id": "halo", "width": 832, "height": 1216, "seed": seed, "turbo": False,
           "steps": 40, "cfg": 4.0, "prompt": prompt, "model": runner.ANIMA29,
           "prefix": "halo"}
    wf = runner.build_workflow(job)
    if extra_neg:
        wf["21"]["inputs"]["text"] = wf["21"]["inputs"]["text"] + ", " + extra_neg
    c.free(unload_models=True, free_memory=True)
    time.sleep(2)
    t0 = time.time()
    qid = c.submit(wf)
    entry, _ = c.wait(qid)
    st = c.status_str(entry)
    if st != "success":
        print("  %-18s FAILED %s" % (label, c.error_messages(entry)))
        continue
    img = c.output_images(entry)[0]
    src = runner.RAW_OUT / img.get("subfolder", "") / img["filename"]
    dst = ROOT / "_stage" / ("halo_%d.png" % len(made))
    dst.write_bytes(Path(src).read_bytes())
    made.append((label, dst))
    print("  %-18s %-8s %.0fs  -> %s" % (label, st, time.time() - t0, dst.name))

if len(made) == 2:
    a, b = (Image.open(p).convert("RGB") for _l, p in made)
    W = 600
    tiles = [im.resize((W, int(im.height * W / im.width)), Image.LANCZOS) for im in (a, b)]
    canvas = Image.new("RGB", (W * 2 + 12, tiles[0].height), (25, 25, 25))
    canvas.paste(tiles[0], (0, 0))
    canvas.paste(tiles[1], (W + 12, 0))
    out = ROOT / "_stage" / "_halo_compare.png"
    canvas.save(out)
    print("-> %s   (左=对照  右=加 halo 负向)" % out.name)
