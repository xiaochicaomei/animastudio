"""Wait for the komi style to be trained+deployed, then run the 40-page manga with it.

Runs unattended: polls for the deployed style, then hands off to orchestrate. Written as a
file because the premise is a long Chinese paragraph that the shell mangles.

    python _stage/run_komi_40pages.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
PY = ROOT / "ComfyUI_windows_portable" / "python_embeded" / "python.exe"
JOBS = ROOT / "workspace" / "jobs"
REG = JOBS / "styles.json"
STYLE = "komi"


def deployed():
    """Registered AND actually built into models/loras."""
    try:
        reg = json.loads(REG.read_text(encoding="utf-8"))
    except Exception:
        return None
    rec = reg.get(STYLE)
    if not rec:
        return None
    if not (ROOT / "models" / "loras" / rec["lora_file"]).exists():
        return None
    return rec


print("=== 等 komi 画风训练 + 部署完成 ===", flush=True)
t0 = time.time()
last = None
while True:
    rec = deployed()
    if rec:
        print("  已部署: %s  base=%s  score=%s" % (rec["lora_file"], rec.get("base"),
                                                  rec.get("score")), flush=True)
        break
    # report the training step roughly once a minute so the log is not silent
    try:
        log = ROOT / "workspace" / "logs" / "train_komi_29b.log"
        fs = open(log, "rb")
        fs.seek(max(0, log.stat().st_size - 6000))
        txt = fs.read().decode("utf-8", "replace")
        fs.close()
        seg = [s for s in txt.split("\r") if "s/it" in s]
        if seg and seg[-1][:60] != last:
            last = seg[-1][:60]
            print("  " + seg[-1].strip()[:110], flush=True)
    except Exception:
        pass
    if time.time() - t0 > 9 * 3600:
        print("  等了 9 小时仍未部署 - 放弃", flush=True)
        sys.exit(2)
    time.sleep(60)

premise = (JOBS / "xiaozi_premise.txt").read_text(encoding="utf-8").strip()
sheet = JOBS / "xiaozi_sheet.txt"

cmd = [
    str(PY), "-u", str(ROOT / "pipeline" / "orchestrate.py"),
    "--premise", premise,
    "--panels", "40",
    "--style", STYLE,
    "--sheet", str(sheet),
    "--adult",
    # one full-resolution panel per page (2.28 MP), 2.9B + the komi style.
    # NO --hires: measured on 2.9B, the refine pass costs +175s per page and softens the
    # hard cel lines that are the whole reason to use the 40-block model. Bare 2.9B is
    # 66s per page and sharper.
    "--no-page-mode", "--cols", "1", "--rows", "1",
]
print()
print("launch: --panels 40 --style komi --no-page-mode --cols 1 --rows 1 --adult", flush=True)
print("", flush=True)
r = subprocess.run(cmd)
print("ORCHESTRATE rc = %d" % r.returncode, flush=True)
sys.exit(r.returncode)
