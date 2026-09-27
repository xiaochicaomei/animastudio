"""Static load-validity check for load_anima_model's config, per checkpoint.

load_anima_model raises when the checkpoint has keys the model lacks ("unexpected") or
when keys the model has are missing from the checkpoint outside a known buffer allowlist.
This rebuilds the same dit_config on the meta device (no memory) and asserts that
condition for every installed Anima checkpoint, so the patch is checked the way the
loader checks it rather than by eyeballing a key count.

Run with the TRAINER venv, not the ComfyUI embedded python - it needs torch, accelerate
and the trainer's own library/:
    trainer\venv\Scripts\python.exe pipeline\test_anima_blocks.py

It guards the local patch in trainer\sd-scripts\library\anima_utils.py that reads
num_blocks from the checkpoint instead of hardcoding 28. If sd-scripts is ever updated
and that patch is lost, a 2.9B checkpoint stops loading with "Unexpected keys in
checkpoint: blocks.28..." - this test says so in one line instead.
"""
import re
import sys
from pathlib import Path

from safetensors import safe_open
from accelerate import init_empty_weights

SD = Path(r"D:\AnimaStudio\trainer\sd-scripts")
sys.path.insert(0, str(SD))

from library.anima_utils import count_dit_blocks, DEFAULT_NUM_BLOCKS  # noqa: E402
from library import anima_models  # noqa: E402

MODELS = Path(r"D:\AnimaStudio\models\diffusion_models")

# mirrors anima_utils.load_anima_model, including the patched num_blocks line
FIXED = dict(
    max_img_h=512, max_img_w=512, max_frames=128, in_channels=16, out_channels=16,
    patch_spatial=2, patch_temporal=1, model_channels=2048, concat_padding_mask=True,
    crossattn_emb_channels=1024, pos_emb_cls="rope3d", pos_emb_learnable=True,
    pos_emb_interpolation="crop", min_fps=1, max_fps=30, use_adaln_lora=True,
    adaln_lora_dim=256, num_heads=16, extra_per_block_abs_pos_emb=False,
    rope_h_extrapolation_ratio=4.0, rope_w_extrapolation_ratio=4.0,
    rope_t_extrapolation_ratio=1.0, extra_h_extrapolation_ratio=1.0,
    extra_w_extrapolation_ratio=1.0, extra_t_extrapolation_ratio=1.0,
    rope_enable_fps_modulation=False, use_llm_adapter=True,
    attn_mode="torch", split_attn=False,
)
BUFFER_ALLOW = ("seq", "dim_spatial_range", "dim_temporal_range", "inv_freq")


def rename(key):
    for pre in ("net.", "model.diffusion_model."):
        if key.startswith(pre):
            return key[len(pre):]
    return key


ok = True
for path in sorted(MODELS.glob("anima-*.safetensors")):
    with safe_open(str(path), framework="pt") as f:
        ckpt = {rename(k) for k in f.keys()}

    blocks = count_dit_blocks(str(path)) or DEFAULT_NUM_BLOCKS
    with init_empty_weights():
        model = anima_models.Anima(num_blocks=blocks, **FIXED)
    mk = set(model.state_dict().keys())

    unexpected = sorted(ckpt - mk)
    missing = sorted(mk - ckpt)
    bad_missing = [k for k in missing if not any(b in k for b in BUFFER_ALLOW)]

    good = not unexpected and not bad_missing
    ok = ok and good
    print("%s %-36s blocks=%-3d ckpt=%-4d model=%-4d unexpected=%d missing=%d (unsaved buffers=%d)"
          % ("OK " if good else "BAD", path.name, blocks, len(ckpt), len(mk),
             len(unexpected), len(bad_missing), len(missing) - len(bad_missing)))
    if unexpected:
        print("      unexpected e.g. %s" % unexpected[:4])
    if bad_missing:
        print("      missing e.g. %s" % bad_missing[:4])

print()
print("RESULT:", "PASS - every checkpoint loads into its inferred config" if ok else "FAIL")
sys.exit(0 if ok else 1)
