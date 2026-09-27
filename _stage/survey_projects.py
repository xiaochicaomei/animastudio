"""Survey open-source projects in the same space, through the desktop Clash proxy.

Goal is not a reading list: for each project I want the one thing it does better than
AnimaStudio, because that is what can actually be stolen.
"""
import json
import time
import urllib.parse
import urllib.request

PX = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
OP = urllib.request.build_opener(urllib.request.ProxyHandler(PX))
OP.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
                 ("Accept", "application/vnd.github+json")]

QUERIES = [
    "manga generation LLM diffusion pipeline",
    "comic generation AI agent storyboard",
    "story to comic diffusion model",
    "AI manga creator automatic",
    "character consistency comic generation",
    "ComfyUI manga pipeline workflow",
    "panel layout generation diffusion",
    "automatic comic authoring tool",
]


def gh(path):
    for a in range(3):
        try:
            with OP.open("https://api.github.com" + path, timeout=45) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if a == 2:
                return {"error": str(e)[:80]}
            time.sleep(2)


seen = {}
for q in QUERIES:
    u = "/search/repositories?q=" + urllib.parse.quote(q) + "&sort=stars&per_page=8"
    j = gh(u)
    if "items" not in j:
        print("!! %-46s %s" % (q, j.get("error") or j.get("message")))
        continue
    print("=== %s  (%d hits) ===" % (q, j.get("total_count", 0)))
    for it in j["items"]:
        fn = it["full_name"]
        seen.setdefault(fn, it)
        print("   %-42s %6d*  %-12s %s" % (
            fn, it["stargazers_count"], (it.get("language") or "-"),
            (it.get("description") or "")[:86]))
    print()
    time.sleep(1)

print("=" * 96)
print("去重后共 %d 个项目，按星数排：" % len(seen))
for fn, it in sorted(seen.items(), key=lambda kv: -kv[1]["stargazers_count"])[:28]:
    print("  %6d*  %-42s %s" % (it["stargazers_count"], fn,
                                it.get("pushed_at", "")[:10]))
