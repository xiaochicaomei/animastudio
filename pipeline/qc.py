"""AnimaStudio QC pass: the supervision layer.

Runs after a render pass, purely on CPU, and reports - it never decides to
re-render by itself. The agent reads qc.json and issues rerolls through
control.json, which is what keeps the pipeline auditable.

Checks per panel:
  1. technical  : decodable, non-blank, resolution matches the job spec
  2. duplicate  : dHash Hamming distance against other panels
  3. rating     : the WD tagger's rating is recorded as metadata only - there is no gate
  4. tag hit    : how many of the job's requested tags the tagger confirms (advisory)
  5. text       : comic text/bubble detector - pseudo-text is a hard failure only
                  with --text-gate, otherwise it is reported and left to the judge

CLI:
  python qc.py                     # check every panel in out/panels
  python qc.py --job p001          # check one
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ROOT = Path(r"D:\AnimaStudio")
JOBS = ROOT / "workspace" / "jobs" / "jobs.jsonl"
STATE = ROOT / "workspace" / "jobs" / "state.json"
QC = ROOT / "workspace" / "jobs" / "qc.json"
PANELS = ROOT / "workspace" / "out" / "panels"
TEXT_MODEL = ROOT / "models" / "qc" / "comic-text-detector.onnx"

# NOTE: there is deliberately no allowed-rating list here. AnimaStudio does not
# gate content: the tagger's rating is written into qc.json for information, and
# what gets rendered is the caller's decision alone.

# The planner's tag list mixes three different things: layout instructions
# ("2koma", "multiple views"), free-text action phrases ("girl walks in carrying
# backpack") and real booru tags ("black school blazer"). Only the last group can
# ever come back from a WD tagger, so measuring coverage across all three used to
# produce fake 0/8 alarms. Layout tags and the LoRA trigger are dropped by name,
# and everything outside the tagger's own vocabulary is dropped as free text.
LAYOUT_TAGS = {"comic", "2koma", "4koma", "multiple views", "border", "panel layout"}


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def load_jobs():
    out = {}
    if not JOBS.exists():
        return out
    for line in JOBS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            job = json.loads(line)
        except Exception:
            continue
        if job.get("id"):
            out[job["id"]] = job
    return out


def dhash(img, size=8):
    """Difference hash: robust, dependency-free near-duplicate detection."""
    from PIL import Image

    g = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = g.tobytes()  # stable across Pillow versions
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


def technical_checks(path, job):
    from PIL import Image, ImageStat

    res = {"ok": True, "reasons": [], "width": None, "height": None}
    try:
        with Image.open(path) as im:
            im.load()
            res["width"], res["height"] = im.size
            stat = ImageStat.Stat(im.convert("RGB"))
            spread = max(stat.stddev)
            if spread < 3.0:
                res["ok"] = False
                res["reasons"].append("blank_or_flat(stddev=%.2f)" % spread)
            if job:
                ew, eh = int(job.get("width", 1024)), int(job.get("height", 1024))
                if (res["width"], res["height"]) != (ew, eh):
                    res["ok"] = False
                    res["reasons"].append("size_mismatch(%dx%d!=%dx%d)"
                                          % (res["width"], res["height"], ew, eh))
    except Exception as e:
        res["ok"] = False
        res["reasons"].append("decode_failed:%s" % e)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default=None)
    ap.add_argument("--general-th", type=float, default=0.35)
    ap.add_argument("--text-th", type=float, default=0.5,
                    help="score threshold for the comic text/bubble detector")
    ap.add_argument("--text-gate", action="store_true",
                    help="treat detected text as a hard failure (default: reported only)")
    args = ap.parse_args()

    jobs = load_jobs()
    state = read_json(STATE, {})
    qc_prev = read_json(QC, {})

    targets = []
    for p in sorted(PANELS.glob("*.png")):
        jid = p.stem
        if args.job and jid != args.job:
            continue
        targets.append((jid, p))
    if not targets:
        print("no panels found in %s" % PANELS)
        return 1

    hashes = {}
    for jid, p in targets:
        hashes[jid] = dhash(__import__("PIL.Image", fromlist=["Image"]).open(p))

    from wd_tagger import WDTagger

    tagger = WDTagger()
    # only category 0 (general) tags can ever come back from predict(), so that is
    # exactly the set of intents this pipeline is able to verify
    general_vocab = {n.replace("_", " ") for n, c in zip(tagger.names, tagger.cats) if c == 0}

    # The text detector is a separate download, so a missing model disables that one
    # check rather than failing the whole pass.
    text_detector = None
    if TEXT_MODEL.exists():
        try:
            from text_detect import TextDetector

            text_detector = TextDetector()
        except Exception as e:
            print("text detector unavailable (%s) - skipping the text check" % e)
    else:
        print("text detector model missing at %s - skipping the text check" % TEXT_MODEL)

    report = dict(qc_prev)
    hard_prefixes = ["blank_or_flat", "size_mismatch", "decode_failed"]
    if args.text_gate:
        # text_free only: pseudo-glyphs inside a bubble are expected and are painted over
        # when the page is lettered, so gating on them would fail every page with a line.
        hard_prefixes.append("text_free")
    hard_prefixes = tuple(hard_prefixes)
    n_pass = n_fail = 0

    for jid, p in targets:
        job = jobs.get(jid)
        rec = {"ts": __import__("time").strftime("%Y-%m-%d %H:%M:%S"), "file": str(p)}
        tech = technical_checks(p, job)
        rec["technical"] = tech

        ratings = tagger.predict(p, general_th=args.general_th)
        rec["rating"] = ratings["top_rating"]
        rec["rating_scores"] = ratings["rating"]
        rec["tags_detected"] = ratings["general"][:40]

        if text_detector is not None:
            try:
                td = text_detector.predict(p, score_th=args.text_th)
                rec["text_counts"] = td["counts"]
                rec["text_boxes"] = td["hits"][:6]
            except Exception as e:
                print("  %s text detection failed: %s" % (jid, e))

        reasons = list(tech["reasons"])

        # The rating is collected above and left in the report; it is never turned
        # into a failure reason.
        #
        # The three classes mean very different things now that page mode asks for a
        # speech bubble on every page that carries a line:
        #   bubble       an empty bubble. Legitimate - compose.py letters into it.
        #   text_bubble  pseudo-glyphs the model drew inside one. Also expected: the
        #                model fills every bubble it draws, and compose.py paints them
        #                all white before writing the real line. Not a defect.
        #   text_free    glyphs on the artwork itself (a corner signature, a fake
        #                username). This is the class that survives lettering, so it is
        #                the one worth gating on - and only --text-gate does that.
        counts = rec.get("text_counts") or {}
        n_bubble = int(counts.get("bubble", 0))
        n_in_bubble = int(counts.get("text_bubble", 0))
        n_free = int(counts.get("text_free", 0))
        n_text = n_in_bubble + n_free
        if n_text:
            reasons.append("text_detected:%d (in_bubble=%d, free=%d)"
                           % (n_text, n_in_bubble, n_free))
        # only the on-artwork class is gateable; see --text-gate above
        if n_free:
            reasons.append("text_free:%d" % n_free)

        # advisory: near duplicates
        for other, h in hashes.items():
            if other != jid and hamming(hashes[jid], h) <= 4:
                reasons.append("near_duplicate_of:%s" % other)

        # advisory: intent coverage, measured only over tags this pipeline can verify
        if job:
            # the prompt is the source of truth for intent. job["tags"] only ever held
            # a prefix of it - and for page-mode or hand-written jobs that prefix is
            # layout tags and free text, i.e. nothing that can be verified.
            src = job.get("prompt") or ", ".join(job.get("tags") or [])
            want = [t.strip().lower().replace("_", " ") for t in src.split(",") if t.strip()]
            if want:
                got = set(t.replace("_", " ") for t in ratings["general"])
                measurable = [t for t in want
                              if t not in LAYOUT_TAGS and not t.startswith("style_")
                              and (not general_vocab or t in general_vocab)]
                ignored = [t for t in want if t not in measurable]
                if ignored:
                    rec["tag_ignored"] = ignored[:12]
                if measurable:
                    hit = sum(1 for t in measurable if t in got)
                    rec["tag_want"] = measurable
                    rec["tag_hit"] = "%d/%d" % (hit, len(measurable))
                    if hit == 0 and len(measurable) >= 2:
                        reasons.append("tag_hit_zero")

        hard = [r for r in reasons if r.startswith(hard_prefixes)]
        rec["reasons"] = reasons
        rec["status"] = "fail" if hard else "pass"
        report[jid] = rec
        if rec["status"] == "pass":
            n_pass += 1
            print("PASS %-8s rating=%-12s tags=%-3d text=%-2d bub=%-2d %s"
                  % (jid, rec["rating"], len(ratings["general"]), n_text, n_bubble,
                     ("hit=" + rec["tag_hit"]) if rec.get("tag_hit") else ""))
        else:
            n_fail += 1
            print("FAIL %-8s rating=%-12s text=%-2d bub=%-2d reasons=%s"
                  % (jid, rec["rating"], n_text, n_bubble, hard))

    QC.parent.mkdir(parents=True, exist_ok=True)
    QC.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("qc summary: pass=%d fail=%d -> %s" % (n_pass, n_fail, QC))
    return 0 if n_fail == 0 else 3


if __name__ == "__main__":
    sys.exit(main())