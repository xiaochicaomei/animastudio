"""Verify the grid publisher: N panels -> N/(cols*rows) pages at native panel size."""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
PY = ROOT / "ComfyUI_windows_portable" / "python_embeded" / "python.exe"
OUT = ROOT / "_stage" / "_gridpages"
OUT.mkdir(parents=True, exist_ok=True)
for old in OUT.glob("*.png"):
    old.unlink()

panels = sorted(p.stem for p in (ROOT / "workspace" / "out" / "panels").glob("p0*f.png"))
print("可用正式档: %d 张" % len(panels))

cols, rows = 2, 2
per = cols * rows
groups = [panels[i:i + per] for i in range(0, len(panels), per)]
groups = [g for g in groups if len(g) == per]
print("拼成 %d 页（每页 %dx%d 格）" % (len(groups), cols, rows))
print()

made = []
for n, grp in enumerate(groups, 1):
    out = OUT / ("page_%03d.png" % n)
    cmd = [str(PY), "-X", "utf8", str(ROOT / "pipeline" / "compose.py"),
           "--cols", str(cols), "--rows", str(rows),
           "--panels", ",".join(grp), "--out", str(out)]
    if n == 1:
        cmd += ["--title", "秀子的秘密花园"]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    last = ((r.stdout or "").strip().splitlines() or ["(no stdout)"])[-1]
    print("  第%02d页 %5.1fs rc=%d  %s" % (n, time.time() - t0, r.returncode, last[:96]))
    if r.returncode == 0:
        made.append(out)

print()
from PIL import Image
for f in made[:4]:
    im = Image.open(f)
    print("  %-14s %dx%d  %.1f MP" % (f.name, im.width, im.height, im.width * im.height / 1e6))
print()
print("RESULT:", "PASS - %d 页全部拼出，画布按面板原尺寸" % len(made) if made else "FAIL")
sys.exit(0 if made else 1)
