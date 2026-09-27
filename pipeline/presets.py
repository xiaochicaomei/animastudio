"""AnimaStudio preset library: community prompt-kit data, ready for the planner.

Built by build_presets.py from the AnimaPromptKit data files plus the user's own
outfit tag list. The kits ship a ComfyUI prompt panel; AnimaStudio already has a
prompt assembler, so only their DATA is taken here.

Three levers, all opt-in per run:

  artist   ``@artist`` style tags. The Anima model card calls this the strongest
           style lever and states the effect is very weak without the ``@``. Each
           entry carries ``posts`` = how often that artist appears in Anima's
           training corpus, which is a direct measure of whether the model will
           recognise the name at all. Higher is safer.
  preset   a ready-made tag block plus its natural-language sentence. The model card
           asks for detailed natural language alongside tags, and the kits ship
           exactly that pairing.
  negative a vetted negative pack from the kit.

Content note: minor-coded sexual content was filtered out by build_presets.py, and
the removals are recorded in the JSON under "filtered" so the filter can be audited.

CLI:
  python presets.py --list [--kind mature]
  python presets.py --show 雨中回眸
  python presets.py --artists 40
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
DATA = ROOT / "pipeline" / "presets" / "presets.json"

_CACHE = None


def load():
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(DATA.read_text(encoding="utf-8"))
    return _CACHE


def available():
    return DATA.exists()


def artists(limit=None):
    a = load().get("artists") or []
    return a[:limit] if limit else a


def find_artist(name):
    """Exact match first, then case-insensitive, because the table mixes cases."""
    table = load().get("artists") or []
    for a in table:
        if a["name"] == name:
            return a
    low = name.lower()
    for a in table:
        if a["name"].lower() == low:
            return a
    return None


def presets(kind=None, nsfw=None):
    out = load().get("presets") or []
    if kind:
        kinds = {kind} if isinstance(kind, str) else set(kind)
        out = [p for p in out if p["kind"] in kinds]
    if nsfw is not None:
        out = [p for p in out if bool(p.get("nsfw")) == nsfw]
    return out


def find_preset(name):
    """Exact, then case-insensitive, then substring - kit names are long and Chinese."""
    table = load().get("presets") or []
    for p in table:
        if p["name"] == name:
            return p
    low = name.lower()
    for p in table:
        if p["name"].lower() == low:
            return p
    hits = [p for p in table if low in p["name"].lower() or low in (p.get("note") or "").lower()]
    return hits[0] if len(hits) == 1 else None


def negative_packs():
    return load().get("negatives") or {}


def _bigrams(s):
    s = "".join(ch for ch in str(s or "") if not ch.isspace())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else ({s} if s else set())


def search(query, k=14, kinds=None):
    """Lexical shortlist for a story: Chinese bigram overlap, highest first.

    Deliberately dumb and local. The model's job is to pick from this shortlist, not to
    remember 466 entries - and since its pick is validated against the library, an
    invented name can never reach a render. A name hit counts double: the name is the
    human-facing intent, the tags are only vocabulary.
    """
    q = _bigrams(query)
    if not q:
        return []
    scored = []
    for p in presets(kinds):
        hay = " ".join([p["name"], p.get("note") or "", p.get("text") or "",
                        p.get("tags") or ""])
        overlap = len(q & _bigrams(hay))
        if not overlap:
            continue
        scored.append((overlap + 2 * len(q & _bigrams(p["name"])), p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:k]]


_STYLES = None


def artist_styles():
    """name -> {"posts", "style"} for the artists the vision index has covered.

    Separate file from presets.json because it is built by a different, much slower job
    (pipeline/build_artist_styles.py) that can be interrupted and resumed at will.
    """
    global _STYLES
    if _STYLES is None:
        p = DATA.parent / "artist_styles.json"
        if p.exists():
            _STYLES = json.loads(p.read_text(encoding="utf-8")).get("artists") or {}
        else:
            _STYLES = {}
    return _STYLES


def artist_menu(limit=60):
    """Compact (name, style) rows for the model to choose from.

    The model cannot be handed 1200 artists and does not need to be: the table is ranked
    by how well Anima knows each name, so its head is also the safe end. Only artists the
    vision index has actually described can appear, because a bare name gives the model
    nothing to reason about - which is the whole reason that index exists.
    """
    rows = [(n, v) for n, v in artist_styles().items() if v.get("style")]
    rows.sort(key=lambda kv: -int(kv[1].get("posts") or 0))
    return [(n, v["style"]) for n, v in rows[:limit]]


def tag_groups():
    return load().get("tag_groups") or []


def artist_tags(names):
    """Turn requested artist names into prompt tags, reporting unknown ones."""
    tags, unknown = [], []
    for raw in names:
        n = raw.strip().lstrip("@")
        if not n:
            continue
        if find_artist(n) is None:
            unknown.append(n)
        tags.append("@" + n)
    return tags, unknown


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--kind", default=None)
    ap.add_argument("--nsfw", action="store_true", help="with --list: only adult presets")
    ap.add_argument("--show", default=None)
    ap.add_argument("--artists", type=int, default=None)
    ap.add_argument("--negatives", action="store_true")
    ap.add_argument("--groups", action="store_true")
    args = ap.parse_args()

    if not available():
        print("no preset library yet - run build_presets.py first")
        return 1

    if args.artists:
        for a in artists(args.artists):
            print("  %-44s %6d posts" % (a["name"], a["posts"]))
        return 0

    if args.negatives:
        for n, t in negative_packs().items():
            print("  %-16s %s" % (n, t))
        return 0

    if args.groups:
        for g in tag_groups():
            print("  %-24s %d tags" % (g["name"], len(g["tags"])))
        return 0

    if args.show:
        p = find_preset(args.show)
        if not p:
            print("no unique preset matches %r" % args.show)
            return 1
        print("%s  [%s%s]" % (p["name"], p["kind"], ", nsfw" if p["nsfw"] else ""))
        print("  tags: %s" % p["tags"])
        if p["text"]:
            print("  text: %s" % p["text"])
        if p["note"]:
            print("  note: %s" % p["note"])
        return 0

    if args.list:
        rows = presets(args.kind, nsfw=True if args.nsfw else None)
        print("%d preset(s)" % len(rows))
        for p in rows:
            print("  [%-11s]%s %s" % (p["kind"], " nsfw" if p["nsfw"] else "    ", p["name"]))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
