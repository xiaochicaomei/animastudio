"""Second pass: name the widget values via ComfyUI's /object_info, and check which
custom nodes each dropped workflow needs that this install does not have.

That second question decides usefulness: a workflow whose nodes are missing cannot run
here at all, however good its sampler settings look.
"""
import json
import urllib.request
from collections import Counter
from pathlib import Path

DL = Path(r"C:\Users\f'h\Downloads")
FILES = [
    "Anima文生图_极简版.json",
    "Anima二次元.json",
    "Z-IMAGE：小而美图像模型：文生图 (1).json",
    "黑科技加速双采-self-lift工作流+(1).json",
]

print("fetching /object_info ...")
with urllib.request.urlopen("http://127.0.0.1:8188/object_info", timeout=120) as r:
    INFO = json.loads(r.read().decode("utf-8"))
print("  installed node types: %d" % len(INFO))
print()

INTEREST = {"steps", "cfg", "sampler_name", "scheduler", "denoise", "scale_by",
            "ckpt_name", "unet_name", "lora_name", "vae_name", "clip_name", "text",
            "width", "height", "seed", "noise_seed", "add_noise", "start_at_step",
            "end_at_step", "return_with_leftover_noise", "batch_size", "strength",
            "model_name", "lora_1", "lora_2", "lora_3", "lora_4", "positive", "negative"}


def widget_names(class_type):
    d = INFO.get(class_type)
    if not d:
        return None
    req = (d.get("input") or {}).get("required") or {}
    opt = (d.get("input") or {}).get("optional") or {}
    names = []
    for src in (req, opt):
        for k, v in src.items():
            t = v[0] if isinstance(v, list) and v else v
            # widget inputs are scalars / combos; links are separate
            if isinstance(t, list) or t in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"):
                names.append(k)
    return names


for fn in FILES:
    p = DL / fn
    obj = json.loads(p.read_text(encoding="utf-8-sig"))
    print("=" * 90)
    print("### %s" % fn)
    nodes = obj.get("nodes") or []
    types = [n.get("type") for n in nodes if isinstance(n, dict)]
    missing = sorted({t for t in types if t and t not in INFO})
    print("   节点 %d 个, 本机缺失 %d 种: %s" % (len(types), len(missing), missing or "无"))
    print("   可运行性: %s" % ("✅ 全部节点都在" if not missing else "❌ 缺节点，需装包或改图"))
    print("   关键参数:")
    seen = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        ct = n.get("type")
        wv = n.get("widgets_values")
        if not wv:
            continue
        names = widget_names(ct) or []
        pairs = list(zip(names, wv))
        for k, v in pairs:
            if k in INTEREST:
                key = (ct, k)
                if key in seen:
                    continue
                seen.add(key)
                if isinstance(v, str) and len(v) > 70:
                    v = v[:70] + "..."
                print("      %-26s %-18s = %s" % (ct, k, v))
    print()
