"""Measure the local brain: prefill and decode separately, straight from llama-server.

Why prefill and decode are reported apart: this machine has 15.8 GiB of RAM and the
model is memory-mapped by default, so the weights live in evictable page cache - which
Windows may compress or drop between tokens. Wall-clock time alone cannot tell you
whether that hurts, because prefill and decode load the machine in different ways:
prefill touches nearly every expert (many tokens, each routing on its own) while decode
touches only the few experts one token needs. If a flag change helps one and hurts the
other, a single total would hide it.

llama-server reports both halves in `timings`, so this just sends a fixed request and
prints them. The prompt is fixed on purpose: the A/B is only meaningful if the only
thing that changes between runs is the server configuration.

CLI:
  python bench_agent.py --label mmap --runs 3
"""
import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent  # noqa: E402  - for the real system prompt and the server URL

URL = "http://127.0.0.1:8080/v1/chat/completions"

# Roughly the shape of a real page-mode plan call: the real system prompt, four beats,
# and the JSON template. Kept short of the full ctx so the KV cache stays comparable.
#
# Three different stories on purpose: a lookup-based drafter (ngram-*) caches n-grams
# across requests, so reusing one prompt makes runs 2 and 3 artificially fast and the
# result means nothing. Different stories with the same JSON scaffolding is what a real
# batch looks like - and the shared scaffolding is exactly where such a drafter can win.
STORIES = [
    ["放学后，一个女生在教室窗台发现一只蜷着的橘猫",
     "她蹲下来伸手，猫蹭了蹭她的手指",
     "她把猫塞进书包，拉链拉到一半停住",
     "走廊尽头传来脚步声，她抱紧书包站直"],
    ["午休时，男生在图书馆角落翻到一本没有书名的旧册子",
     "册子里夹着一张泛黄的照片，上面是陌生的校服",
     "他抬头，发现管理员正隔着书架看着他",
     "管理员轻轻摇头，把手指放在唇边"],
    ["雨天，便利店门口，一个女孩把伞借给了淋湿的同学",
     "同学道谢后跑开，伞柄上留着一张写有地址的纸条",
     "她按地址找到一栋老房子，门开着",
     "屋里的老人说，这把伞等了很多年"],
]


def build_prompt(story=0):
    beats = "\n".join("  Page %d: %s" % (i + 1, b)
                      for i, b in enumerate(STORIES[story % len(STORIES)]))
    user = (
        "First define the character sheet.\n"
        "Draw 4 manga page(s), numbered 1..4 of 4. Each page is ONE illustration that "
        "carries its own 2-4 panels; the beat tells you what happens on that page.\n"
        "Beats:\n" + beats + "\n"
        "Reply with JSON only:\n"
        '{"character_sheet": {"appearance": ["hair tag", "eye tag", "outfit tag"],\n'
        '    "props": ["colour + size + kind"]},\n'
        ' "pages": [{"id": 1, "panel_count": 2,\n'
        '   "shot": "wide shot|medium shot|close-up", "scene": "where this beat happens",\n'
        '   "pose_action": ["4-6 tags"], "tags": ["character sheet tags"]}]}\n'
    )
    return [{"role": "system", "content": agent.SYSTEM_PAGE},
            {"role": "user", "content": user}]


def one_call(messages, max_tokens):
    body = {"messages": messages, "max_tokens": max_tokens, "temperature": 0,
            "stream": False, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("  HTTP %s: %s" % (e.code, e.read().decode()[:300]))
        return None
    wall = time.time() - t0
    t = res.get("timings") or {}
    usage = res.get("usage") or {}
    return {
        "wall": wall,
        "prompt_n": t.get("prompt_n") or usage.get("prompt_tokens"),
        "prefill_tps": t.get("prompt_per_second"),
        "decode_n": t.get("predicted_n") or usage.get("completion_tokens"),
        "decode_tps": t.get("predicted_per_second"),
        "finish": res["choices"][0].get("finish_reason"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=400)
    args = ap.parse_args()

    rows = []
    for i in range(1, args.runs + 1):
        # A different story per run. Identical prompts would let both the server's KV
        # cache and a lookup-based drafter reuse earlier work, so the run would measure
        # caching instead of the machine - which is exactly the trap this guards.
        messages = build_prompt(i - 1)
        messages[-1]["content"] += "\n<!-- bench run %d -->" % i
        m = one_call(messages, args.max_tokens)
        if not m:
            return 2
        rows.append(m)
        print("  %s run %d: wall %6.1fs | prefill %4d tok @ %6.1f tok/s | "
              "decode %3d tok @ %5.2f tok/s | %s"
              % (args.label, i, m["wall"], m["prompt_n"], m["prefill_tps"] or 0,
                 m["decode_n"], m["decode_tps"] or 0, m["finish"]))

    pre = [r["prefill_tps"] for r in rows if r["prefill_tps"]]
    dec = [r["decode_tps"] for r in rows if r["decode_tps"]]
    wall = [r["wall"] for r in rows]

    def med(xs):
        return statistics.median(xs) if xs else 0.0

    def spread(xs):
        return (max(xs) - min(xs)) / med(xs) * 100 if len(xs) > 1 and med(xs) else 0.0

    print("  %-8s prefill %6.1f tok/s (±%.0f%%) | decode %5.2f tok/s (±%.0f%%) | wall %5.1fs"
          % (args.label, med(pre), spread(pre), med(dec), spread(dec), med(wall)))
    # one machine-readable line so a before/after pair can be compared without parsing prose
    print("SUMMARY %s prefill_med=%.2f prefill_spread=%.1f decode_med=%.3f "
          "decode_spread=%.1f wall_med=%.2f"
          % (args.label, med(pre), spread(pre), med(dec), spread(dec), med(wall)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
