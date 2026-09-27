"""Build a style index for the artist table, using the vision model already running.

The problem
-----------
The artist table has 1200 entries and each one carries only a name and a post count. A
post count answers "does Anima recognise this name at all", but says nothing about what
the name *looks like*, so automatic artist selection has nothing to reason about. There
is no public artist-to-style dataset either: the Anima Style Gallery is a JS app with no
API, and both artist tables in the kit are name-and-count only.

The way out
-----------
The kit's data file also carries an official sample image per artist on the CDN. So the
index is built locally: download the sample, hand it to the vision model that is already
attached to llama-server via --mmproj, and ask for style keywords only. Retrieval then
matches story text against those keywords, exactly like the preset shortlist does - and
the keyword text is what lets a model pick an artist for a mood instead of by popularity.

The samples are downscaled to 512 first. Same measurement as the judge: image tokens
dominate prefill cost, and style is legible at 512.

Resumable by design: images are cached on disk and finished artists are skipped, so a
re-run after an interruption costs nothing for the work already done.

CLI:
  python build_artist_styles.py --limit 80
  python build_artist_styles.py --limit 3 --verbose
"""
import argparse
import base64
import io
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
PIPE = ROOT / "pipeline"
sys.path.insert(0, str(PIPE))
import agent  # noqa: E402  (chat + the log helper)

OUT = PIPE / "presets" / "artist_styles.json"
CACHE = ROOT / "_stage" / "artist_samples"
KIT_DATA = ROOT / "_stage" / "promptkit" / "ComfyUI-AnimaPromptKit" / "anima_artists_data.py"

ASK = ("Look at this illustration and describe ONLY its art style - brushwork, colour "
       "palette, lighting, line weight, shading, texture. Do not describe the subject, "
       "the character or the scene. Answer with 4 to 6 comma-separated Simplified "
       "Chinese keywords and nothing else.")


def load_artists():
    """(name, posts, sample_url) from the kit's data file."""
    import importlib.util
    if not KIT_DATA.exists():
        return []
    spec = importlib.util.spec_from_file_location("ad", KIT_DATA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [tuple(row[:3]) for row in getattr(mod, "ANIMA_ARTISTS", [])]


def safe_name(name):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def fetch(url, path):
    """GET, not HEAD: this CDN answers 403 to HEAD but serves GET normally."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    path.write_bytes(data)
    return len(data)


def data_url(path, max_side):
    from PIL import Image
    img = Image.open(path).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        s = max_side / float(max(w, h))
        img = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def describe(url):
    msgs = [{"role": "system", "content":
             "You describe the art style of images. You answer with comma-separated "
             "keywords only - no sentences, no preamble, no markdown."},
            {"role": "user", "content": [
                {"type": "text", "text": ASK},
                {"type": "image_url", "image_url": {"url": url}}]}]
    raw = agent.chat(msgs, max_tokens=90)
    # the model occasionally wraps a sentence around the keywords; keep it to one line
    line = " ".join(str(raw or "").split())
    for junk in ("画风：", "风格：", "关键词：", "答：", "Answer:", "Style:"):
        if line.startswith(junk):
            line = line[len(junk):].strip()
    return line.strip("。. \u3000")[:120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=80,
                    help="how many artists, most-recognised first (the table is ranked)")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--max-side", type=int, default=512)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)

    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    entries = data.setdefault("artists", {})
    data["model"] = "Qwen3.5-9B + mmproj-F16 (local vision)"
    data["note"] = ("Style keywords per artist, derived locally from the official sample "
                    "image. No public artist->style dataset exists; this is the index that "
                    "makes automatic artist selection possible.")

    targets = load_artists()[:args.limit]
    if not targets:
        agent.log("no artist table found at %s" % KIT_DATA)
        return 1

    todo = [t for t in targets if t[0] not in entries]
    agent.log("artist style index: %d requested, %d already done, %d to go"
              % (len(targets), len(targets) - len(todo), len(todo)))

    for i, (name, posts, url) in enumerate(todo, 1):
        img = CACHE / (safe_name(name) + ".avif")
        try:
            if not img.exists():
                fetch(url, img)
            style = describe(data_url(img, args.max_side))
        except Exception as e:
            agent.log("  [%d/%d] %s FAILED: %s" % (i, len(todo), name, str(e)[:110]))
            img.unlink(missing_ok=True)   # a half-written sample must not poison a retry
            continue
        entries[name] = {"posts": posts, "style": style}
        # save after every artist: this job runs for a long time on a slow CDN
        data["generated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        agent.log("  [%d/%d] %-30s %s" % (i, len(todo), name, style))

    agent.log("artist style index done: %d artists -> %s" % (len(entries), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
