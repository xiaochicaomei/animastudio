"""Launch the 40-page manga as ONE unattended orchestrate pass.

This is the whole-system test: story -> plan -> draft render -> QC -> review ->
finalize -> final render (hires) -> judge -> reroll -> compose pages, with the local
agent making every decision and orchestrate handing the single GPU between the agent
and the renderer at each stage boundary.

Written as a file rather than a PowerShell one-liner because the premise is a long
Chinese paragraph: passing it through the shell mangled it in an earlier attempt.

    python _stage\\run_40pages.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
PY = ROOT / "ComfyUI_windows_portable" / "python_embeded" / "python.exe"
JOBS = ROOT / "workspace" / "jobs"

premise = (JOBS / "xiaozi_premise.txt").read_text(encoding="utf-8").strip()
sheet = JOBS / "xiaozi_sheet.txt"

cmd = [
    str(PY), "-u", str(ROOT / "pipeline" / "orchestrate.py"),
    "--premise", premise,
    "--panels", "40",
    "--style", "sodalord_style",
    "--sheet", str(sheet),
    "--hires", "1.5",
    "--adult",
    # Per-panel rendering, one panel per page. The 4-beat A/B measured it: page mode packs
    # 2-4 sub-panels into one 2.28 MP sheet (0.91 MP per scene) and the model often repeats
    # the same shot across them, while a per-panel render gives that whole 2.28 MP to one
    # scene. cols=1 rows=1 makes each page exactly one full-resolution panel, which is also
    # the "one image = one page, with text" shape the project started from.
    "--no-page-mode", "--cols", "1", "--rows", "1",
    # nearest-exact is the setting the A/B was run with; bislerp is added but unproven, so
    # it does not get to ride along on a 105-minute run.
    "--hires-method", "nearest-exact",
]
print("story premise : %s..." % premise[:40], flush=True)
print("sheet         : %s" % sheet.read_text(encoding="utf-8").strip(), flush=True)
print("launch        : --panels 40 --no-page-mode --cols 1 --rows 1 --hires 1.5 --adult",
      flush=True)
print("", flush=True)

r = subprocess.run(cmd)
print("ORCHESTRATE rc = %d" % r.returncode, flush=True)
sys.exit(r.returncode)
