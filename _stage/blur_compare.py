"""Side-by-side 1:1 crop: draft vs hires final, so "is it blurry" is measured, not argued.

Both images cover the same composition (final is exactly 1.5x the draft), so the same
relative crop shows the same content at the same display scale. Also prints a Laplacian
variance (sharpness) per crop - higher means more high-frequency detail.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

P = Path(r"D:\AnimaStudio\workspace\out\panels")
PAIRS = [(a, b) for a, b in (("p001", "p001f"), ("p002", "p002f")) if (P / (b + ".png")).exists()]
# relative crop box (x0, y0, x1, y1) in 0..1 - a face, where blur is most visible
CROP = (0.16, 0.06, 0.44, 0.30)
OUT_W = 520


def sharpness(im):
    g = np.asarray(im.convert("L"), dtype=np.float32)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


tiles = []
for a, b in PAIRS:
    da, fb = Image.open(P / (a + ".png")).convert("RGB"), Image.open(P / (b + ".png")).convert("RGB")
    print("%s %s   vs   %s %s" % (a, da.size, b, fb.size))
    row = []
    for label, im in (("draft", da), ("final+hires", fb)):
        w, h = im.size
        box = (int(CROP[0] * w), int(CROP[1] * h), int(CROP[2] * w), int(CROP[3] * h))
        c = im.crop(box)
        # resample BOTH to the same display width so we compare content, not scale
        c = c.resize((OUT_W, int(c.height * OUT_W / c.width)), Image.LANCZOS)
        # the draft crop is 1/1.5 the pixels of the final crop: show that as the note
        print("   %-12s crop %dx%d  -> shown %dx%d   sharpness=%.0f"
              % (label, box[2] - box[0], box[3] - box[1], c.width, c.height, sharpness(c)))
        d = ImageDraw.Draw(c)
        d.rectangle([0, 0, c.width - 1, 26], fill=(0, 0, 0))
        d.text((8, 6), "%s  %s" % (a, label), fill=(255, 255, 255))
        row.append(c)
    hh = max(t.height for t in row)
    canvas = Image.new("RGB", (OUT_W * 2 + 12, hh), (30, 30, 30))
    canvas.paste(row[0], (0, 0))
    canvas.paste(row[1], (OUT_W + 12, 0))
    tiles.append(canvas)

tot_h = sum(t.height for t in tiles) + 12 * (len(tiles) - 1)
final = Image.new("RGB", (tiles[0].width, tot_h), (30, 30, 30))
y = 0
for t in tiles:
    final.paste(t, (0, y))
    y += t.height + 12
out = Path(r"D:\AnimaStudio\_stage\_blur_compare.png")
final.save(out)
print("->", out)
