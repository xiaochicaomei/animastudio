"""Round two: the components AnimaStudio is weakest at, not the whole-pipeline niche.

Round one showed the "AI comic pipeline" niche is hobby-scale (12 stars max). The real
accumulated work sits in the pieces, so search for those by name and by concept.
"""
import json
import time
import urllib.parse
import urllib.request

PX = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
OP = urllib.request.build_opener(urllib.request.ProxyHandler(PX))
OP.addheaders = [("User-Agent", "Mozilla/5.0"), ("Accept", "application/vnd.github+json")]

NAMED = ["StoryDiffusion", "StoryMaker", "DreamO", "InstantID", "IP-Adapter",
         "consistory", "manga-image-translator", "Anima LoRA", "comic-text-detector",
         "manga panel detector", "webtoon generation", "storyboard generation LLM"]

CONCEPT = [
    "consistent character generation story",
    "identity preserving diffusion reference",
    "layout aware image generation",
    "multimodal story visualization",
    "diffusion comic page layout",
]


def gh(path):
    for a in range(3):
        try:
            with OP.open("https://api.github.com" + path, timeout=45) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if a == 2:
                return {"error": str(e)[:70]}
            time.sleep(2)


def show(label, q, n=6, sort="stars"):
    j = gh("/search/repositories?q=" + urllib.parse.quote(q) + "&sort=%s&per_page=%d" % (sort, n))
    if "items" not in j:
        print("!! %-30s %s" % (label, j.get("error") or j.get("message")))
        return
    print("=== %s ===" % label)
    for it in j["items"]:
        print("   %-44s %7d*  %s" % (it["full_name"], it["stargazers_count"],
                                     (it.get("description") or "")[:74]))
    print()
    time.sleep(0.8)


for q in NAMED:
    show(q, q + " in:name")
for q in CONCEPT:
    show(q, q)
