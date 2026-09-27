"""Compare the A/B result: page mode pages vs a grid page built from per-panel renders."""
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(r"D:\AnimaStudio")
PY = ROOT / "ComfyUI_windows_portable" / "python_embeded" / "python.exe"
P = ROOT / "workspace" / "out" / "panels"
JOBS = ROOT / "workspace" / "jobs" / "jobs.jsonl"
OUT = ROOT / "_stage"

jobs = {j["id"]: j for j in (json.loads(l) for l in JOBS.read_text(encoding="utf-8").splitlines() if l.strip())}
import re
print("=== A 组：整页（模型自排版）===")
tot_a = 0
for jid in ("p001", "p002", "p003", "p004"):
    p = jobs.get(jid, {}).get("prompt", "")
    m = re.search(r"(\d)koma", p)
    n = int(m.group(1)) if m else 0
    tot_a += n
    im = Image.open(P / (jid + ".png"))
    per = im.width * im.height / max(1, n)
    print("   %-5s %sx%s  内含 %d 格  ->  每格约 %.2f MP" % (jid, im.width, im.height, n, per / 1e6))
print("   合计 %d 个画面，总像素 %.1f MP" % (tot_a, 4 * 2.28))

print()
print("=== B 组：逐格（每张 = 一个完整画面）===")
for jid in ("p005", "p006", "p007", "p008"):
    im = Image.open(P / (jid + ".png"))
    print("   %-5s %sx%s  ->  每格 %.2f MP" % (jid, im.width, im.height, im.width * im.height / 1e6))
print("   合计 4 个画面，总像素 %.1f MP" % (4 * 2.28))

print()
print("=== 拼 B 组为 2x2 页 ===")
grid = OUT / "_ab_grid_B.png"
r = subprocess.run([str(PY), "-X", "utf8", str(ROOT / "pipeline" / "compose.py"),
                    "--cols", "2", "--rows", "2",
                    "--panels", "p005,p006,p007,p008", "--out", str(grid),
                    "--title", "雨夜"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
print("   " + ((r.stdout or "").strip().splitlines() or ["(none)"])[-1][:120])

# side-by-side preview at the same display width
W = 620
tiles = []
for label, f in (("A  整页 page mode", P / "p001.png"), ("B  逐格+拼版", grid)):
    im = Image.open(f).convert("RGB")
    c = im.resize((W, int(im.height * W / im.width)), Image.LANCZOS)
    d = ImageDraw.Draw(c)
    d.rectangle([0, 0, c.width - 1, 30], fill=(0, 0, 0))
    d.text((10, 9), "%s   %dx%d" % (label, im.width, im.height), fill=(255, 255, 255))
    tiles.append(c)
h = max(t.height for t in tiles)
canvas = Image.new("RGB", (W * 2 + 14, h), (25, 25, 25))
canvas.paste(tiles[0], (0, 0))
canvas.paste(tiles[1], (W + 14, 0))
out = OUT / "_ab_compare.png"
canvas.save(out)
print()
print("   ->", out.name)
