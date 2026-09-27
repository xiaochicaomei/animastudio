"""Render the B group: Anima-2.9B, no style LoRA, no hires.

Same beat, same seed, same prompt tail as an existing 2B render, so the only differences
are the base model and the refine pass. That isolates the trade the decision rests on:
2B aesthetic + style LoRA + hires (2.28 MP) versus 2.9B bare (1.01 MP).

    python _stage/render_B_29b.py [reference_job_id]
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
sys.path.insert(0, str(ROOT / "pipeline"))
import runner  # noqa: E402
from comfy import Comfy  # noqa: E402

ref_id = sys.argv[1] if len(sys.argv) > 1 else "p001f"
jobs = {j["id"]: j for j in (json.loads(l) for l in
                             (ROOT / "workspace" / "jobs" / "jobs.jsonl")
                             .read_text(encoding="utf-8").splitlines() if l.strip())}
ref = jobs.get(ref_id)
if not ref:
    print("reference job %s not found in the queue" % ref_id)
    raise SystemExit(2)

prompt = ref.get("prompt", "")
# drop the style trigger so the LoRA is out of the picture, and drop the trigger word's
# own tag list is left alone - the sheet tags are what keep the character recognisable
for drop in ("style_sodalord_style, ", "style_orig_student, "):
    prompt = prompt.replace(drop, "")

job = {
    "id": "B29b",
    "width": ref.get("width", 832),
    "height": ref.get("height", 1216),
    "seed": ref.get("seed", 1),
    "turbo": False,
    "steps": 40,
    "cfg": 4.0,
    "prompt": prompt,
    "model": runner.ANIMA29,     # explicit: overrides pick_unet entirely
    "prefix": "B29b",
    # no style_lora: that is the point of the B arm. hires is ON this time - the whole
    # question is whether ComfyUI's async-offload memory stack lets the 2.9B model hold
    # a 1.5x refine pass, which it could not do with no memory flags at all.
    "hires": 1.5,
    "hires_denoise": 0.25,
    "hires_method": "nearest-exact",
}
wf = runner.build_workflow(job)
print("unet   : %s" % wf["1"]["inputs"]["unet_name"])
print("size   : %sx%s" % (wf["30"]["inputs"]["width"], wf["30"]["inputs"]["height"]))
print("hires  : %s" % ("90" in wf and "91" in wf))
print("lora   : %s" % (wf["10"]["inputs"].get("lora_name")))
print()

c = Comfy()
c.free(unload_models=True, free_memory=True)
time.sleep(3)
t0 = time.time()
qid = c.submit(wf)
entry, minfree = c.wait(qid)
st = c.status_str(entry)
imgs = c.output_images(entry)
print("status=%s  %.1fs  peak VRAM free=%s" % (st, time.time() - t0, minfree))
if st != "success":
    print("errors:", c.error_messages(entry))
    raise SystemExit(1)
src = runner.RAW_OUT / imgs[0].get("subfolder", "") / imgs[0]["filename"]
dst = ROOT / "_stage" / "B_29b.png"
dst.write_bytes(Path(src).read_bytes())
from PIL import Image
im = Image.open(dst)
print("-> %s  %dx%d  %.2f MP" % (dst, im.width, im.height, im.width * im.height / 1e6))
