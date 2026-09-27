"""Read safetensors headers only (no tensor data) to compare the two Anima architectures."""
import json
import re
import sys

from safetensors import safe_open

PATHS = {
    "base 2B (28blk)": r"D:\AnimaStudio\models\diffusion_models\anima-base-v1.0.safetensors",
    "aesthetic (28blk)": r"D:\AnimaStudio\models\diffusion_models\anima-aesthetic-v1.1.safetensors",
    "2.9b (40blk?)": r"D:\AnimaStudio\models\diffusion_models\anima-2.9b-preview-v1.safetensors",
}


def rename(key):
    for pre in ("net.", "model.diffusion_model."):
        if key.startswith(pre):
            return key[len(pre):]
    return key


for label, path in PATHS.items():
    try:
        with safe_open(path, framework="pt") as f:
            keys = list(f.keys())
            meta = f.metadata()
    except Exception as e:
        print("%-18s FAILED: %s" % (label, e))
        continue

    top = [rename(k) for k in keys]
    blocks = [int(m.group(1)) for k in top
              if (m := re.match(r"^blocks\.(\d+)\.", k))]
    nested = sorted({k.split(".blocks.")[0] for k in top if ".blocks." in k and not k.startswith("blocks.")})

    emb = [(k, rename(k)) for k in keys if rename(k).endswith("x_embedder.proj.1.weight")]
    chan = None
    if emb:
        with safe_open(path, framework="pt") as f:
            chan = f.get_slice(emb[0][0]).get_shape()[0]

    print("%-18s tensors=%-5d top-level blocks=%-3s (0..%s)  model_channels=%s"
          % (label, len(keys), len(set(blocks)),
             max(blocks) if blocks else "-", chan))
    print("%-18s nested 'blocks' owners: %s" % ("", nested or "none"))
    print("%-18s metadata keys: %s" % ("", list((meta or {}).keys())[:8]))
    print()
