"""Regression test for base-aware checkpoint routing (runner.adapter_family / pick_unet).

IMPORTANT: this test points runner.STYLE_REG at a temporary registry file rather than
monkeypatching runner.style_registry. An earlier version replaced the function with a
lambda, which meant the real implementation never ran - and it shipped with a missing
`global` that raised UnboundLocalError the first time a 40-page run reached its finals
stage. A test that stubs out the thing under test cannot catch that.

Run with the ComfyUI embedded python:
    python pipeline\\test_routing.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner  # noqa: E402

REGISTRY = {
    "old_style": {"lora_file": "style_orig_student.safetensors"},           # legacy, no base
    "new_style": {"lora_file": "style_sodalord_style.safetensors",
                  "base": "anima-2.9b-preview-v1.safetensors"},
    "aes_style": {"lora_file": "style_x.safetensors",
                  "base": "anima-aesthetic-v1.1.safetensors"},
}

# real file on disk, real mtime, real cache -> the production code path
tmp = Path(tempfile.mkdtemp()) / "styles.json"
tmp.write_text(json.dumps(REGISTRY), encoding="utf-8")
runner.STYLE_REG = tmp
runner._STYLE_CACHE = (None, None)

# (label, job, family, pick_unet, build_workflow node1)
# pick_unet and build_workflow are asserted separately because they legitimately differ
# for ref_images: pick_unet resolves the candidate list, and build_workflow then pins the
# unrefined base, because the in-context LoRA is trained against base. Asserting only one
# of the two is how a "MISMATCH" that is not a bug gets mistaken for one.
CASES = [
    ("no adapters", {}, None,
     "anima-2.9b-preview-v1.safetensors", "anima-2.9b-preview-v1.safetensors"),
    ("legacy style (no base field)", {"style_lora": "style_orig_student.safetensors"},
     "2b", "anima-aesthetic-v1.1.safetensors", "anima-aesthetic-v1.1.safetensors"),
    ("2.9B-trained style", {"style_lora": "style_sodalord_style.safetensors"},
     "29", "anima-2.9b-preview-v1.safetensors", "anima-2.9b-preview-v1.safetensors"),
    ("aesthetic-trained style", {"style_lora": "style_x.safetensors"},
     "2b", "anima-aesthetic-v1.1.safetensors", "anima-aesthetic-v1.1.safetensors"),
    ("unregistered lora", {"style_lora": "someone_elses.safetensors"},
     "2b", "anima-aesthetic-v1.1.safetensors", "anima-aesthetic-v1.1.safetensors"),
    ("ref_images", {"style_lora": "style_sodalord_style.safetensors",
                    "ref_images": ["a.png"]},
     "2b", "anima-aesthetic-v1.1.safetensors", "anima-base-v1.0.safetensors"),
    ("pose_image", {"pose_image": "p.png"},
     "2b", "anima-aesthetic-v1.1.safetensors", "anima-aesthetic-v1.1.safetensors"),
    ("2.9B style + ref_images", {"style_lora": "style_sodalord_style.safetensors",
                                 "ref_images": ["a.png"]},
     "2b", "anima-aesthetic-v1.1.safetensors", "anima-base-v1.0.safetensors"),
]

fails = []
print("%-30s %-6s %-22s %-22s" % ("case", "family", "pick_unet", "build_workflow"))
print("-" * 86)
for label, job, want_fam, want_pick, want_wf in CASES:
    j = dict(job)
    j.setdefault("id", "t")
    fam = runner.adapter_family(j)
    picked = runner.pick_unet(runner.UNET_FINAL_CANDIDATES, j)
    try:
        used = runner.build_workflow(j)["1"]["inputs"]["unet_name"]
        raised = None
    except Exception as e:
        used, raised = None, e
    bad = []
    if fam != want_fam:
        bad.append("family=%s want %s" % (fam, want_fam))
    if picked != want_pick:
        bad.append("pick_unet=%s want %s" % (picked, want_pick))
    if raised is not None:
        bad.append("build_workflow raised %s" % type(raised).__name__)
    elif used != want_wf:
        bad.append("build_workflow=%s want %s" % (used, want_wf))
    if bad:
        fails.append("%s: %s" % (label, "; ".join(bad)))
    print("%-30s %-6s %-22s %-22s %s"
          % (label, fam,
             (picked or "-").replace("anima-", "").replace(".safetensors", ""),
             ((used or "RAISED").replace("anima-", "").replace(".safetensors", "")),
             "OK" if not bad else "FAIL: " + "; ".join(bad)))

print()
# the mtime cache must actually notice a rewrite, or a concurrent deploy is invisible
time.sleep(1.1)
REGISTRY["new_style"]["base"] = "anima-aesthetic-v1.1.safetensors"
tmp.write_text(json.dumps(REGISTRY), encoding="utf-8")
fam = runner.adapter_family({"id": "t", "style_lora": "style_sodalord_style.safetensors"})
print("cache invalidation: base changed 2.9b -> aesthetic, family now = %s" % fam)
if fam != "2b":
    fails.append("style_registry() served a stale registry after the file changed")

print()
print("draft pick (no job)      ->", runner.pick_unet(runner.UNET_DRAFT_CANDIDATES))
print("draft pick (styled job)  ->", runner.pick_unet(runner.UNET_DRAFT_CANDIDATES,
                                                    {"id": "t", "style_lora": "x"}))
if runner.pick_unet(runner.UNET_DRAFT_CANDIDATES) != "anima-turbo-v1.1.safetensors":
    fails.append("drafts no longer pick the turbo checkpoint")

print()
if fails:
    print("RESULT: FAIL")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("RESULT: PASS - routing, build_workflow and cache invalidation all correct")
