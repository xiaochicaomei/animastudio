"""Search for how to run a hires/refine pass when the base model already fills the card."""
import html
import re
import time
import urllib.parse
import urllib.request

PX = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
OP = urllib.request.build_opener(urllib.request.ProxyHandler(PX))
OP.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36")]

QUERIES = [
    "ComfyUI hires fix low VRAM out of memory two stage upscale",
    "ComfyUI --lowvram --reserve-vram model offload second pass",
    "ComfyUI tiled upscale refine large image 8GB VRAM",
    "ComfyUI two pass hires fix VRAM saving technique",
    "comfyui 二段式 放大 显存不足 解决",
]


def ddg(q):
    u = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q)
    try:
        with OP.open(u, timeout=45) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        return "!!" + str(e)[:60]


for q in QUERIES:
    print("=" * 92)
    print("### %s" % q)
    body = ddg(q)
    if body.startswith("!!"):
        print("   ", body)
        continue
    # result titles + snippets
    hits = re.findall(r'result__a[^>]*>(.*?)</a>.*?result__snippet[^>]*>(.*?)</a>',
                      body, re.S)
    if not hits:
        hits = [(m, "") for m in re.findall(r'result__a[^>]*>(.*?)</a>', body, re.S)]
    seen = 0
    for title, snip in hits[:5]:
        t = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        s = html.unescape(re.sub(r"<[^>]+>", "", snip)).strip()
        print("   • %s" % t[:110])
        if s:
            print("     %s" % s[:190])
        seen += 1
    if not seen:
        print("   （无结果）")
    print()
    time.sleep(2)
