"""Comic text / speech-bubble detector for the AnimaStudio QC pass.

Anima cannot render readable glyphs, so the negative prompt bans text and speech
bubbles - but a diffusion model trained on booru data still invents pseudo-text,
corner logos, signatures and dates. This catches that class of defect.

RT-DETR-v2 trained on comic pages, run through onnxruntime on the CPU exactly like
the WD tagger, so it never touches the 8GB VRAM budget. Three classes:
  bubble       an empty bubble shape
  text_bubble  text drawn inside a bubble
  text_free    text drawn straight onto the artwork

CLI:
  python text_detect.py --image path.png
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(r"D:\AnimaStudio")
DEFAULT_MODEL = ROOT / "models" / "qc" / "comic-text-detector.onnx"

IMAGE_SIZE = 640   # the model's preprocessor resizes to 640x640, no padding
CLASSES = ("bubble", "text_bubble", "text_free")


class TextDetector:
    def __init__(self, model_path=DEFAULT_MODEL, providers=None):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = 8
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(model_path), sess_options=so, providers=providers or ["CPUExecutionProvider"]
        )

    def predict(self, image_path, score_th=0.5):
        from PIL import Image

        with Image.open(image_path) as im:
            return self.detect(im, score_th)

    def detect(self, im, score_th=0.5):
        """Same thing, but for an image already in memory (compose.py hands over a
        finished page after drawing it)."""
        from PIL import Image

        w, h = im.size
        img = im.convert("RGB").resize((IMAGE_SIZE, IMAGE_SIZE), Image.BILINEAR)
        x = np.asarray(img, dtype=np.float32) / 255.0
        x = np.ascontiguousarray(x.transpose(2, 0, 1)[None, ...])

        # The exported graph already contains the sigmoid and scales the boxes back to
        # the original pixel size (xyxy), but it needs that size as a second input.
        #
        # That input is (width, height), NOT (height, width). Passing it the other way
        # round silently swaps x and y: measured on an 832x1216 page, [[h, w]] returned
        # boxes reaching x=1218 (wider than the page) while y stopped at 833, and
        # [[w, h]] put x in [0, 833] and y in [0, 1218]. Nothing raised - the boxes just
        # pointed at the wrong half of the image, which is how compose.py came to paint
        # its white bubble over the wrong region and letter the line in the wrong place.
        labels, boxes, scores = self.session.run(
            None,
            {"images": x, "orig_target_sizes": np.array([[w, h]], dtype=np.int64)},
        )
        labels, boxes, scores = labels[0], boxes[0], scores[0]

        hits = []
        for i in range(len(scores)):
            s = float(scores[i])
            if s < score_th:
                continue
            x1, y1, x2, y2 = (float(v) for v in boxes[i])
            hits.append({"label": CLASSES[int(labels[i])], "score": round(s, 3),
                         "box": [int(x1), int(y1), int(x2), int(y2)]})
        hits.sort(key=lambda d: -d["score"])

        counts = dict.fromkeys(CLASSES, 0)
        for hit in hits:
            counts[hit["label"]] += 1
        return {"counts": counts, "total": len(hits), "hits": hits[:20]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--dir")
    ap.add_argument("--score-th", type=float, default=0.5)
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    args = ap.parse_args()

    detector = TextDetector(args.model)
    if args.image:
        print(json.dumps(detector.predict(args.image, args.score_th),
                         ensure_ascii=False, indent=2))
        return 0
    if args.dir:
        exts = {".png", ".jpg", ".jpeg", ".webp"}
        paths = sorted(p for p in Path(args.dir).iterdir() if p.suffix.lower() in exts)
        print("found %d images in %s" % (len(paths), args.dir))
        for p in paths:
            r = detector.predict(p, args.score_th)
            print("  %-24s text=%d %s" % (p.name, r["total"], r["counts"]))
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
