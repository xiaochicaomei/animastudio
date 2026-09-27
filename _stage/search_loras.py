import html
import re
import time
import urllib.parse
import urllib.request

PX = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
OP = urllib.request.build_opener(urllib.request.ProxyHandler(PX))
OP.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36")]

Q = [
    "CunnyFunky lora civitai",
    "CunnyFunky stable diffusion",
    "miaomiao realskin lora",
    "MiaoMiao RealSkin civitai lora",
    "realskin lora stable diffusion what does it do",
]


def ddg(q):
    u = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q)
    try:
        with OP.open(u, timeout=45) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        return "!!" + str(e)[:60]


for q in Q:
    print("=" * 90)
    print("### %s" % q)
    b = ddg(q)
    if b.startswith("!!"):
        print("   ", b); continue
    hits = re.findall(r'result__a[^>]*>(.*?)</a>.*?result__snippet[^>]*>(.*?)</a>', b, re.S)
    if not hits:
        hits = [(m, "") for m in re.findall(r'result__a[^>]*>(.*?)</a>', b, re.S)]
    if not hits:
        print("   （无结果 —— 名称可能不存在）")
    for t, s in hits[:5]:
        T = html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
        S = html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
        print("   • %s" % T[:105])
        if S:
            print("     %s" % S[:175])
    print()
    time.sleep(2)
