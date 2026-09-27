"""Small end-to-end check of both fixes before committing to another 40-page run.

Checks the two things that were wrong:
  1. the beats carry physical acts instead of mood          (--adult on the story stage)
  2. the planner renders them explicitly and writes dialogue (--adult on plan + the
     reworded page-mode dialogue rule)

Runs story+plan at 4 panels against a throwaway jobs file, so it cannot touch the real
queue. Usage:  python _stage\\test_adult_plan.py
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
sys.path.insert(0, str(ROOT / "pipeline"))
import agent  # noqa: E402

tmp = Path(tempfile.mkdtemp())
agent.JOBS = tmp / "jobs.jsonl"
agent.CONTROL = tmp / "control.json" if hasattr(agent, "CONTROL") else agent.JOBS
story = tmp / "story.txt"
sheet = ROOT / "workspace" / "jobs" / "xiaozi_sheet.txt"
premise = (ROOT / "workspace" / "jobs" / "xiaozi_premise.txt").read_text(encoding="utf-8").strip()

print("=== 1) story --adult (4 panels) ===")
sys.argv = ["agent.py", "story", "--premise", premise, "--panels", "4",
            "--out", str(story), "--sheet", str(sheet), "--adult"]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = agent.main()
print("   rc =", rc)
print(story.read_text(encoding="utf-8").strip())
print()

print("=== 2) plan --adult --page-mode (4 pages) ===")
sys.argv = ["agent.py", "plan", "--story", str(story), "--panels", "4", "--draft",
            "--page-mode", "--width", "832", "--height", "1216",
            "--style", "sodalord_style", "--sheet", str(sheet), "--adult"]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = agent.main()
print("   rc =", rc)
for line in buf.getvalue().splitlines():
    print("   " + line.strip())
print()

jobs = [json.loads(l) for l in agent.JOBS.read_text(encoding="utf-8").splitlines() if l.strip()]
nal = 0
PHYS = ("sex", "nude", "nipple", "breast", "penis", "vagina", "cum", "penetrat", "fellatio",
        "cunnilingus", "masturbat", "areola", "pussy", "cock", "ass", "top-down bottom-up",
        "hetero", "uncensored", "pubic", "saliva", "sweat")
print("=== 3) 结果 ===")
for j in jobs:
    p = j.get("prompt", "")
    hits = [t for t in PHYS if t in p]
    if hits:
        nal += 1
    print("--- %s  dialogue=%r" % (j["id"], (j.get("dialogue") or "")[:30]))
    print("    成人标签: %s" % (", ".join(hits) if hits else "无"))
    print("    prompt  : %s" % p[:230])
print()
print("   带成人标签的页: %d / %d" % (nal, len(jobs)))
print("   带台词的页    : %d / %d" % (sum(1 for j in jobs if (j.get("dialogue") or "").strip()), len(jobs)))
