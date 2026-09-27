"""Verify every product name in the pasted list. Same lesson as last time: an
AI-written survey mixes real projects with invented ones, and the invented ones are
indistinguishable by tone."""
import json
import time
import urllib.parse
import urllib.request

PX = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
OP = urllib.request.build_opener(urllib.request.ProxyHandler(PX))
OP.addheaders = [("User-Agent", "Mozilla/5.0"), ("Accept", "application/vnd.github+json")]

NAMES = [
    "good-comfyui-mcp", "comfyui-mcp", "MPWE", "LocalMiniDrama", "comicmaster",
    "comfyui-auto-drama", "comfyui-scene-composer", "Awesome AI Hentai",
    "awesome-ai-hentai", "Qwen3.5-9B-Uncensored", "Qwen3.5-Uncensored",
    "WAI-NSFW-illustrious", "Pony Realism",
]


def gh(path):
    for a in range(3):
        try:
            with OP.open("https://api.github.com" + path, timeout=40) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if a == 2:
                return {"error": str(e)[:60]}
            time.sleep(3)


for q in NAMES:
    j = gh("/search/repositories?q=" + urllib.parse.quote(q) + "&sort=stars&per_page=4")
    if "items" not in j:
        print("%-26s !! %s" % (q, j.get("error") or j.get("message")))
        time.sleep(2)
        continue
    tc = j.get("total_count", 0)
    if tc == 0:
        print("%-26s ✗ GitHub 0 命中 —— 查无此项目" % q)
    else:
        print("%-26s %d 命中:" % (q, tc))
        for it in j["items"][:3]:
            print("      %-44s %6d*  %s" % (it["full_name"], it["stargazers_count"],
                                            (it.get("description") or "")[:62]))
    time.sleep(1.2)
