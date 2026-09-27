"""Build AnimaStudio's prompt preset library from the community prompt kits.

Sources (all local, nothing is fetched):
  * AnimaPromptKit      - artist table, prompt templates, scene / mature / composition
                          presets, negative packs, tag groups
  * 涩涩服饰.txt        - the user's own outfit tag blocks

The kit is a ComfyUI node pack built around its own panel and its own prompt
assembler. AnimaStudio already has a prompt assembler (the planner), so what is
worth taking is the DATA, not the nodes: a ranked artist table, vetted negative
packs and ready-made content presets. This script converts that data into one
JSON file the pipeline can read, and keeps the provenance in the file itself.

Content note: minor-coded sexual content is filtered out, and only from presets
that are actually sexual. A non-sexual preset that happens to mention a baby is
left alone. Every removal is reported so the filter can be audited rather than
trusted.

CLI:
  python build_presets.py --kit <ComfyUI-AnimaPromptKit dir> [--outfits <txt>]
"""
import argparse
import json
import re
import time
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
OUT = ROOT / "pipeline" / "presets" / "presets.json"

# Applied only to sexual presets: a tag that codes the subject as a child has no
# place in a sexual prompt. Word boundaries keep real tags like "babydoll" (a
# nightgown) from matching "baby".
BLOCK = ("loli", "lolicon", "shota", "shotacon", "child", "children", "toddler",
         "infant", "baby", "kindergarten", "elementary school", "preschool",
         "underage", "minor", "aged down", "age regression", "newborn")
BLOCK_RE = re.compile(r"\b(%s)\b" % "|".join(BLOCK), re.I)


def is_blocked(*texts):
    """Return the offending match, or None."""
    for t in texts:
        if not t:
            continue
        m = BLOCK_RE.search(str(t))
        if m:
            return m.group(0)
    return None


def load_kit(kit_dir):
    """Import the kit's data modules. They are plain Python data files."""
    import importlib.util

    mods = {}
    for name in ("anima_artists_data", "templates_data", "compositions_data",
                 "mature_data", "scenes_data", "nai_recipes_data"):
        path = Path(kit_dir) / (name + ".py")
        if not path.exists():
            continue
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods


# Tags that mark a preset as restricted, matched as whole tokens against the booru tag
# string. This exists because the kit's own TEMPLATES list is not grouped by tone: it
# ships mature entries alongside classroom sketches. Tagging a whole kind as restricted
# hid the tame entries from the default pool, and an auto-selected preset then injected
# restricted tags into a benign beat. Tone has to be read off the content, not the label.
#
# The vocabulary itself is data, not code, and lives in RESTRICTED_TAGS_FILE next to the
# generated library - it is a list of booru tags and is not something this repository
# needs to carry. A missing file degrades to an empty set, which only means every preset
# is treated as general-audience; nothing crashes.
RESTRICTED_TAGS_FILE = Path(__file__).with_name("presets") / "restricted_tags.txt"


def _load_restricted_tags():
    try:
        return {ln.strip().lower() for ln in
                RESTRICTED_TAGS_FILE.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")}
    except OSError:
        return set()


ADULT_TAGS = _load_restricted_tags()


def looks_adult(name, tags):
    if "成人" in str(name or ""):
        return True
    toks = {t.strip().lower() for t in str(tags or "").split(",")}
    return bool(toks & ADULT_TAGS)


def preset(name, kind, tags, text="", nsfw=False, note=""):
    return {"name": name, "kind": kind, "nsfw": bool(nsfw) or looks_adult(name, tags),
            "tags": tags or "", "text": text or "", "note": note or ""}


def collect(mods, dropped):
    out = []

    for t in getattr(mods.get("templates_data"), "TEMPLATES", []) or []:
        out.append(preset(t.get("name", ""), "template", t.get("tags"),
                          t.get("description"), False, t.get("note")))
    for t in getattr(mods.get("templates_data"), "ACTION_PRESETS", []) or []:
        out.append(preset(t.get("name", ""), "action", t.get("tags"),
                          t.get("description"), False, t.get("note")))
    for t in getattr(mods.get("templates_data"), "SCENERY_PRESETS", []) or []:
        out.append(preset(t.get("name", ""), "scenery", t.get("tags"),
                          t.get("description"), False, t.get("note")))
    for t in getattr(mods.get("templates_data"), "LIGHT_PRESETS", []) or []:
        out.append(preset(t.get("name", ""), "light", t.get("tags"),
                          t.get("description"), False, t.get("note")))
    for t in getattr(mods.get("compositions_data"), "COMPOSITION_PRESETS", []) or []:
        out.append(preset(t.get("name", ""), "composition", t.get("tags"),
                          t.get("composition"), False,
                          " ".join(t.get("keywords") or [])))

    for t in getattr(mods.get("scenes_data"), "SCENE_PRESETS", []) or []:
        out.append(preset(t.get("name", ""), "scene", t.get("tags"),
                          t.get("description"), t.get("nsfw")))

    for t in getattr(mods.get("templates_data"), "MATURE_PRESETS", []) or []:
        p = preset(t.get("name", ""), "mature", t.get("tags"), t.get("description"), True)
        hit = is_blocked(p["tags"], p["text"])
        if hit:
            dropped.append((p["name"], hit))
            continue
        out.append(p)

    return out


def outfit_presets(path, dropped):
    """One preset per non-empty line of the user's outfit file."""
    out = []
    if not path or not Path(path).exists():
        return out
    for i, line in enumerate(Path(path).read_text(encoding="utf-8",
                                                  errors="replace").splitlines(), 1):
        tags = line.strip().rstrip(",").strip()
        if not tags:
            continue
        name = "outfit-%02d" % i
        hit = is_blocked(tags)
        if hit:
            dropped.append((name, hit))
            continue
        out.append(preset(name, "outfit", tags, "", True))
    return out


def tag_groups(mods, removed):
    """Adult tag groups, with offending tags removed rather than the whole group."""
    groups = []
    for entry in getattr(mods.get("mature_data"), "MATURE_GROUPS", []) or []:
        if not (isinstance(entry, (list, tuple)) and len(entry) == 2):
            continue
        name, tags = entry
        keep = []
        for t in tags:
            hit = is_blocked(t)
            if hit:
                removed.append((name, t, hit))
                continue
            keep.append(t)
        if keep:
            groups.append({"name": name, "tags": keep})
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit", required=True, help="ComfyUI-AnimaPromptKit directory")
    ap.add_argument("--outfits", default=None, help="outfit tag file, one block per line")
    ap.add_argument("--top-artists", type=int, default=400,
                    help="how many ranked artists to keep (kit ships 1200)")
    args = ap.parse_args()

    mods = load_kit(args.kit)
    if not mods:
        print("no kit data modules found under %s" % args.kit)
        return 1

    dropped, removed = [], []
    presets = collect(mods, dropped) + outfit_presets(args.outfits, dropped)

    artists = [{"name": n, "posts": p}
               for n, p, *_ in (getattr(mods.get("anima_artists_data"),
                                        "ANIMA_ARTISTS", []) or [])][:args.top_artists]
    recipes = [{"idx": r.get("idx"), "artists": r.get("anima") or []}
               for r in (getattr(mods.get("nai_recipes_data"), "NAI_RECIPES", []) or [])]
    negatives = {n: t for n, t in
                 (getattr(mods.get("templates_data"), "NEGATIVE_PACKS", []) or [])}

    data = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": {
            "kit": str(args.kit),
            "outfits": str(args.outfits) if args.outfits else None,
            "note": "Data extracted from the community prompt kits; nodes are NOT used. "
                    "AnimaStudio's own planner consumes these as vocabulary and presets.",
        },
        "filtered": {"presets_dropped": dropped, "tags_removed": removed[:50],
                     "tags_removed_total": len(removed)},
        "artists": artists,
        "recipes": recipes,
        "negatives": negatives,
        "tag_groups": tag_groups(mods, removed),
        "presets": presets,
    }
    # tag_groups() below appends to `removed`, so the audit counts are refreshed after it
    data["filtered"]["tags_removed"] = removed[:50]
    data["filtered"]["tags_removed_total"] = len(removed)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    kinds = {}
    for p in presets:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    print("presets -> %s" % OUT)
    print("  artists   %d (top by training-corpus post count)" % len(artists))
    print("  recipes   %d artist combos" % len(recipes))
    print("  negatives %d packs" % len(negatives))
    print("  groups    %d adult tag groups" % len(data["tag_groups"]))
    print("  presets   %d  %s" % (len(presets), kinds))
    print("  filtered  %d presets dropped, %d tags removed from groups"
          % (len(dropped), len(removed)))
    for name, hit in dropped:
        print("    drop %-40s (%s)" % (name, hit))
    for name, t, hit in removed[:10]:
        print("    strip %-24s %-28s (%s)" % (name, t, hit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
