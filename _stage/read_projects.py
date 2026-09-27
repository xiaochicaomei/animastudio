"""Fetch the READMEs that matter, through the proxy, and pull out what is stealable."""
import re
import time
import urllib.request

PX = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
OP = urllib.request.build_opener(urllib.request.ProxyHandler(PX))
OP.addheaders = [("User-Agent", "Mozilla/5.0")]

REPOS = [
    ("HVision-NKU/StoryDiffusion", "consistent self-attention, training-free"),
    ("univ-esuty/noisecollage", "manga layout control"),
    ("FireRedTeam/StoryMaker", "subject-driven consistency"),
    ("muzishen/IMAGHarmony", "object quantity control"),
]

KEYS = ("train", "attention", "consisten", "layout", "panel", "manga", "comic",
        "reference", "lora", "controlnet", "batch", "id", "identity", "free",
        "install", "require", "vram", "memory")


def get(url):
    for a in range(3):
        try:
            with OP.open(url, timeout=45) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if a == 2:
                return "!! " + str(e)[:70]
            time.sleep(2)


for repo, tag in REPOS:
    print("=" * 96)
    print("### %s   (%s)" % (repo, tag))
    txt = None
    for br in ("main", "master"):
        txt = get("https://raw.githubusercontent.com/%s/%s/README.md" % (repo, br))
        if not txt.startswith("!!") and len(txt) > 300:
            break
    if not txt or txt.startswith("!!"):
        print("   README unavailable:", (txt or "")[:70])
        continue
    lines = [l.rstrip() for l in txt.splitlines()]
    print("   README %d 行" % len(lines))
    # first meaningful paragraph = what it claims to do
    body = [l for l in lines if l.strip() and not l.startswith(("#", "!", "[", "<", "|", "-"))]
    print("   主张:", " ".join(body[:3])[:300] if body else "(none)")
    print("   关键行:")
    shown = 0
    for l in lines:
        s = l.strip()
        if len(s) < 15 or len(s) > 220:
            continue
        if any(k in s.lower() for k in KEYS):
            print("     ", s[:150])
            shown += 1
            if shown >= 14:
                break
    print()
    time.sleep(1)
