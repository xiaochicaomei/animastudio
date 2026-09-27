"""Look up two named resources rather than guessing from the names."""
import html
import json
import re
import time
import urllib.parse
import urllib.request

PX = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
OP = urllib.request.build_opener(urllib.request.ProxyHandler(PX))
OP.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36")]


def get(url, timeout=45):
    try:
        with OP.open(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        return "!!" + str(e)[:80]


def ddg(q):
    body = get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q))
    if body.startswith("!!"):
        return body
    hits = re.findall(r'result__a[^>]*>(.*?)</a>.*?result__snippet[^>]*>(.*?)</a>', body, re.S)
    if not hits:
        hits = [(m, "") for m in re.findall(r'result__a[^>]*>(.*?)</a>', body, re.S)]
    out = []
    for t, s in hits[:6]:
        out.append((html.unescape(re.sub(r"<[^>]+>", "", t)).strip(),
                    html.unescape(re.sub(r"<[^>]+>", "", s)).strip()))
    return out


def civitai(q):
    body = get("https://civitai.com/api/v1/models?limit=6&query=" + urllib.parse.quote(q))
    if body.startswith("!!"):
        return body
    try:
        d = json.loads(body)
    except Exception:
        return "!(unparsable)"
    out = []
    for m in d.get("items", []):
        out.append({
            "name": m.get("name"),
            "type": m.get("type"),
            "nsfw": m.get("nsfw"),
            "tags": (m.get("tags") or [])[:8],
            "creator": (m.get("creator") or {}).get("username"),
            "downloads": m.get("stats", {}).get("downloadCount"),
        })
    return out


for q in ["CunnyFunky", "CunnyFunky lora", "miaomiao realskin", "realskin lora anime"]:
    print("=" * 90)
    print("### %s" % q)
    r = ddg(q)
    if isinstance(r, str):
        print("   ", r)
    elif not r:
        print("    （无结果）")
    else:
        for t, s in r:
            print("   • %s" % t[:105])
            if s:
                print("     %s" % s[:175])
    time.sleep(2)

for q in ["CunnyFunky", "miaomiao", "realskin"]:
    print("=" * 90)
    print("### Civitai API: %s" % q)
    r = civitai(q)
    if isinstance(r, str):
        print("   ", r)
    elif not r:
        print("    （无结果）")
    else:
        for m in r:
            print("   • %-32s type=%-10s nsfw=%-5s by=%s dl=%s" % (
                str(m["name"])[:32], m["type"], m["nsfw"], m["creator"], m["downloads"]))
            if m["tags"]:
                print("     tags: %s" % ", ".join(m["tags"]))
    time.sleep(2)
