"""Stage the desktop collection as a training set: unzip -> PNG -> de-duplicate.

style_train.py's trainer only reads PNG, and two of the five archives look like the same
gallery published twice, so hashing matters: duplicates would silently double the weight
of whatever they depict.

    python _stage/stage_komi.py
"""
import glob
import hashlib
import io
import os
import shutil
import zipfile
from pathlib import Path

from PIL import Image

DESK = Path(os.path.expanduser("~")) / "Desktop" / "收藏"
DST = Path(r"D:\AnimaStudio\workspace\refs\komi")
IMG = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")

DST.mkdir(parents=True, exist_ok=True)
for old in DST.glob("*.png"):
    old.unlink()

seen = {}
kept = skipped_dup = failed = 0
for zp in sorted(glob.glob(str(DESK / "*.zip"))):
    with zipfile.ZipFile(zp) as z:
        for info in z.infolist():
            if info.is_dir() or os.path.splitext(info.filename)[1].lower() not in IMG:
                continue
            try:
                raw = z.read(info)
                im = Image.open(io.BytesIO(raw))
                im.load()
            except Exception:
                failed += 1
                continue
            h = hashlib.md5(raw).hexdigest()
            if h in seen:
                skipped_dup += 1
                continue
            seen[h] = info.filename
            out = DST / ("%05d.png" % (kept + 1))
            im.convert("RGB").save(out, "PNG")
            kept += 1

# the loose webp on the desktop too
for lp in DESK.glob("*.webp"):
    raw = lp.read_bytes()
    h = hashlib.md5(raw).hexdigest()
    if h in seen:
        skipped_dup += 1
        continue
    seen[h] = lp.name
    Image.open(io.BytesIO(raw)).convert("RGB").save(DST / ("%05d.png" % (kept + 1)), "PNG")
    kept += 1

sizes = {}
for p in DST.glob("*.png"):
    with Image.open(p) as im:
        sizes[im.size] = sizes.get(im.size, 0) + 1

print("写入 %s" % DST)
print("  保留      : %d 张" % kept)
print("  重复丢弃  : %d 张" % skipped_dup)
print("  解码失败  : %d 张" % failed)
print("  尺寸分布  :")
for s, n in sorted(sizes.items(), key=lambda kv: -kv[1])[:8]:
    print("     %-12s %d" % ("%dx%d" % s, n))
