import glob
import os
import zipfile

D = os.path.join(os.path.expanduser("~"), "Desktop", "收藏")
IMG = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
tot = 0
for p in sorted(glob.glob(os.path.join(D, "*.zip"))):
    z = zipfile.ZipFile(p)
    names = [i.filename for i in z.infolist() if not i.is_dir()]
    exts = {}
    for n in names:
        e = os.path.splitext(n)[1].lower()
        exts[e] = exts.get(e, 0) + 1
    n_img = sum(v for k, v in exts.items() if k in IMG)
    tot += n_img
    print("%-18s %3d 文件  %s" % (os.path.basename(p), len(names), exts))
    for n in names[:4]:
        print("       ", n)
print()
print("图片总数:", tot)
print("目录:", D)
