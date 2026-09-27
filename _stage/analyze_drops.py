"""Read the dropped workflow JSONs: what model, what settings, what custom nodes.

Handles both ComfyUI formats - the UI graph ({"nodes": [...]}) and the API graph
({"1": {"class_type": ...}}).
"""
import io
import json
import sys
from pathlib import Path

DL = Path(r"C:\Users\f'h\Downloads")
FILES = [
    "Z-IMAGE：小而美图像模型：文生图 (1).json",
    "Anima二次元.json",
    "Anima文生图_极简版.json",
    "黑科技加速双采-self-lift工作流+(1).json",
    "prompt (1).json",
]


def load(p):
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return json.loads(raw.decode(enc))
        except Exception:
            continue
    raise ValueError("cannot decode")


def node_types(obj):
    """-> list of (class_type, title)"""
    out = []
    if isinstance(obj, dict) and "nodes" in obj and isinstance(obj["nodes"], list):
        for n in obj["nodes"]:
            if isinstance(n, dict):
                out.append((n.get("type") or "?", (n.get("title") or "").strip()))
        return out, "UI"
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict) and "class_type" in v:
                out.append((v["class_type"], (v.get("_meta") or {}).get("title", "")))
        if out:
            return out, "API"
    return [], "?"


def find_inputs(obj, keys):
    """collect values of any input whose name is in keys"""
    found = {}
    def walk(o):
        if isinstance(o, dict):
            if "class_type" in o and isinstance(o.get("inputs"), dict):
                for k, v in o["inputs"].items():
                    if k in keys and not isinstance(v, (list, dict)):
                        found.setdefault(k, []).append(v)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return found


NOTE_KEYS = {"ckpt_name", "unet_name", "lora_name", "vae_name", "clip_name",
             "steps", "cfg", "sampler_name", "scheduler", "denoise", "seed",
             "width", "height", "batch_size", "text", "positive", "negative",
             "scale_by", "upscale_method", "model_name", "control_net_name"}
INTEREST = {"steps", "cfg", "sampler_name", "scheduler", "denoise", "scale_by",
            "ckpt_name", "unet_name", "lora_name", "vae_name", "clip_name"}

for fn in FILES:
    p = DL / fn
    print("=" * 88)
    print("### %s  (%d B)" % (fn, p.stat().st_size))
    try:
        obj = load(p)
    except Exception as e:
        print("   parse failed: %s" % e)
        continue
    types, fmt = node_types(obj)
    print("   格式: %s   节点数: %d" % (fmt, len(types)))
    if not types:
        print("   顶层键:", list(obj)[:12] if isinstance(obj, dict) else type(obj))
        if isinstance(obj, dict):
            s = json.dumps(obj, ensure_ascii=False)
            print("   内容预览:", s[:600])
        print()
        continue
    from collections import Counter
    c = Counter(t for t, _ in types)
    print("   节点类型 (%d 种):" % len(c))
    for t, n in c.most_common():
        print("      %-34s x%d" % (t, n))
    vals = find_inputs(obj, NOTE_KEYS)
    print("   关键参数:")
    for k in sorted(vals):
        v = vals[k]
        v = v[:6]
        mark = "  <<<" if k in INTEREST else ""
        print("      %-16s %s%s" % (k, v, mark))
    print()
