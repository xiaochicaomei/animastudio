"""WD-EVA02 tagger for AnimaStudio.

Two jobs, one model:
  1. auto-captioning for style LoRA training (danbooru tags = Anima's prompt language)
  2. reporting the content rating into qc.json (recorded, never enforced)

CPU only (onnxruntime), so it never touches the 8GB VRAM budget.

CLI:
  python wd_tagger.py --image path.png
  python wd_tagger.py --dir folder --write-txt [--prefix-tag style_xxx]
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

IMAGE_SIZE = 448
DEFAULT_MODEL = r"D:\AnimaStudio\trainer\wd_tagger\wd-eva02-model.onnx"
DEFAULT_TAGS = r"D:\AnimaStudio\trainer\wd_tagger\selected_tags.csv"


class WDTagger:
    def __init__(self, model_path=DEFAULT_MODEL, tags_csv=DEFAULT_TAGS, providers=None):
        so = ort.SessionOptions()
        so.intra_op_num_threads = 8
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(model_path), sess_options=so, providers=providers or ["CPUExecutionProvider"]
        )
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.shape = inp.shape  # e.g. [N,3,448,448] (NCHW) or [N,448,448,3] (NHWC)
        self.names, self.cats = self._load_tags(tags_csv)

    @staticmethod
    def _load_tags(csv_path):
        names, cats = [], []
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            head = next(reader)
            i_name = head.index("name")
            i_cat = head.index("category")
            for row in reader:
                names.append(row[i_name])
                cats.append(int(row[i_cat]))
        return names, cats

    def _prepare(self, image_path):
        img = Image.open(image_path).convert("RGB")
        arr = np.array(img)
        h, w = arr.shape[:2]
        size = max(h, w)
        pad_y, pad_x = size - h, size - w
        top, left = pad_y // 2, pad_x // 2
        arr = np.pad(
            arr,
            ((top, pad_y - top), (left, pad_x - left), (0, 0)),
            mode="constant",
            constant_values=255,
        )
        img = Image.fromarray(arr).resize((IMAGE_SIZE, IMAGE_SIZE), Image.BICUBIC)
        x = np.asarray(img, dtype=np.float32)[:, :, ::-1]  # RGB -> BGR
        batch = np.expand_dims(x, 0)
        # adapt to the model's expected layout instead of assuming
        if len(self.shape) == 4 and self.shape[1] == 3:
            batch = np.transpose(batch, (0, 3, 1, 2))  # -> NCHW
        return np.ascontiguousarray(batch)

    def predict(self, image_path, general_th=0.35, character_th=0.85):
        probs = self.session.run(None, {self.input_name: self._prepare(image_path)})[0][0]
        general, character, rating = [], [], {}
        for name, cat, p in zip(self.names, self.cats, probs):
            p = float(p)
            if cat == 9:
                rating[name] = p
            elif cat == 4 and p >= character_th:
                character.append((name, p))
            elif cat == 0 and p >= general_th:
                general.append((name, p))
        general.sort(key=lambda t: -t[1])
        character.sort(key=lambda t: -t[1])
        return {
            "rating": {k: round(v, 3) for k, v in sorted(rating.items(), key=lambda t: -t[1])},
            "top_rating": max(rating, key=rating.get) if rating else None,
            "general": [t[0] for t in general],
            "character": [t[0] for t in character],
        }

    def caption(self, image_path, prefix=None, **kw):
        r = self.predict(image_path, **kw)
        tags = list(r["general"])
        if prefix:
            tags = [prefix] + [t for t in tags if t != prefix]
        return ", ".join(tags)


def _iter_images(folder):
    exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    return sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in exts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--dir")
    ap.add_argument("--write-txt", action="store_true")
    ap.add_argument("--prefix-tag", default=None)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--tags", default=DEFAULT_TAGS)
    args = ap.parse_args()

    tagger = WDTagger(args.model, args.tags)

    if args.image:
        print(json.dumps(tagger.predict(args.image), ensure_ascii=False, indent=2))
        return 0

    if args.dir:
        images = _iter_images(args.dir)
        print("found %d images in %s" % (len(images), args.dir))
        ratings = {}
        for p in images:
            r = tagger.predict(p)
            ratings[r["top_rating"]] = ratings.get(r["top_rating"], 0) + 1
            if args.write_txt:
                txt = p.with_suffix(".txt")
                txt.write_text(tagger.caption(p, prefix=args.prefix_tag), encoding="utf-8")
            print("  %-40s rating=%-12s tags=%d" % (p.name, r["top_rating"], len(r["general"])))
        print("rating summary:", ratings)
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())