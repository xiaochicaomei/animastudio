"""AnimaStudio style trainer: reference images -> deployed style LoRA, autonomously.

Closed loop (each step is a function, each run is logged):
  1. prep     auto-caption the reference set (WD tags = Anima's language)
  2. train    kohya anima_train_network.py, GPU freed from ComfyUI first
  3. convert  kohya LoRA keys -> ComfyUI keys (networks/convert_anima_lora_to_comfy.py)
  4. probe    render a fixed probe grid per checkpoint, score against the reference set
  5. deploy   best checkpoint -> models/loras/style_<name>.safetensors + registration

Scoring is deliberately three cheap CPU metrics, not vibes:
  tag distribution (cosine) 0.5 | colour palette (L1) 0.3 | line density 0.2

Input contract: the reference images must be ones you have the rights to use
(own art, own generations, or licensed). Nothing here fetches third-party art.

CLI:
  python style_train.py run    --name orig_student [--steps 600] [--repeats 6]
  python style_train.py prep   --name orig_student
  python style_train.py probe  --name orig_student
  python style_train.py deploy --name orig_student

--base selects the DiT checkpoint to train against ('base' | 'aesthetic' | '2.9b').
It is recorded in styles.json at deploy time and runner.py routes each job on it, so a
28-block adapter and a 40-block adapter can coexist under the same style name.
"""
import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402
from comfy import Comfy  # noqa: E402
from wd_tagger import WDTagger  # noqa: E402

ROOT = Path(r"D:\AnimaStudio")
MODELS = ROOT / "models"
REFS = ROOT / "workspace" / "refs"
TRAINER = ROOT / "trainer"
SD = TRAINER / "sd-scripts"
VENV_PY = TRAINER / "venv" / "Scripts" / "python.exe"
TRAIN_OUT = TRAINER / "out"
CONFIGS = TRAINER / "configs"
LORAS = MODELS / "loras"
PROBES = ROOT / "workspace" / "out" / "probes"
LOGS = ROOT / "workspace" / "logs"
REG = ROOT / "workspace" / "jobs" / "styles.json"

QWEN3 = MODELS / "text_encoders" / "qwen_3_06b_base.safetensors"
VAE = MODELS / "vae" / "qwen_image_vae.safetensors"

# Which DiT checkpoint a style is trained against. This is part of the style's identity,
# not a convenience flag: Anima-2.9B is a 40-block layer expansion of the 28-block base
# (manifest insertions at 2,5,8,...,36), so an adapter trained on one and applied to the
# other lands on shifted layers WITHOUT raising an error - silent degradation, not a
# crash. Measured earlier: every pre-existing adapter here (style_orig_student,
# style_sodalord_style, anima-incontext-character, anima_pose_preview2, anima-turbo-lora)
# addresses blocks 0-27 only. The chosen base is written into styles.json at deploy time
# and runner.py routes on it, which is what makes the two architectures coexist.
BASES = {
    "base": "anima-base-v1.0.safetensors",
    "aesthetic": "anima-aesthetic-v1.1.safetensors",
    "2.9b": "anima-2.9b-preview-v1.safetensors",
}
DEFAULT_BASE = "base"


def resolve_base(spec=None):
    """'base' | '2.9b' | an exact filename -> (filename, Path).

    Deliberately dumb: an unknown name becomes a missing-file error at train time rather
    than a quiet fallback to a different architecture.
    """
    spec = (spec or DEFAULT_BASE).strip()
    name = BASES.get(spec.lower(), spec)
    if not name.endswith(".safetensors"):
        name += ".safetensors"
    return name, MODELS / "diffusion_models" / name


# Checkpoints of different architectures must never share a directory. cmd_probe globs
# *.safetensors and cmd_deploy copies the winner, so a 2B checkpoint left next to a 2.9B
# run would be probed on the wrong base and could be the one deployed. The 28-block base
# keeps its historical path so nothing already on disk has to move.
BASE_SLUGS = {
    "anima-base-v1.0.safetensors": "",
    "anima-aesthetic-v1.1.safetensors": "_aesthetic",
    "anima-2.9b-preview-v1.safetensors": "_29b",
}


def run_dir(name, base_name):
    slug = BASE_SLUGS.get(base_name, "_" + base_name.replace(".safetensors", "").replace(".", ""))
    return TRAIN_OUT / (name + slug)

PROBE_PROMPTS = [
    ("probe_a", "{t}, 1girl, solo, upper body, gentle smile, classroom, window light"),
    ("probe_b", "{t}, 1girl, solo, full body, standing, school hallway, from side"),
    ("probe_c", "{t}, 1girl, solo, close up on face, surprised expression, outdoors, sunset"),
]

W_TAG, W_PALETTE, W_EDGE = 0.5, 0.3, 0.2


def log(msg, name=None):
    line = "[style %s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    f = LOGS / ("style_%s.log" % name) if name else LOGS / "style_trainer.log"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


# ------------------------------------------------------------------ metrics
def palette_hist(img, bins=16):
    a = np.asarray(img.convert("RGB").resize((128, 128), Image.BILINEAR), dtype=np.float32) / 255.0
    h = []
    for c in range(3):
        hh, _ = np.histogram(a[:, :, c], bins=bins, range=(0.0, 1.0))
        h.append(hh / max(1, a.shape[0] * a.shape[1]))
    return np.concatenate(h)


def edge_density(img):
    g = np.asarray(img.convert("L").resize((256, 256), Image.BILINEAR), dtype=np.float32) / 255.0
    dx = np.abs(np.diff(g, axis=1)).mean()
    dy = np.abs(np.diff(g, axis=0)).mean()
    return float((dx + dy) / 2)


def tag_vector(tag_lists, vocab):
    v = np.zeros(len(vocab), dtype=np.float32)
    for tags in tag_lists:
        for t in tags:
            if t in vocab:
                v[vocab[t]] += 1.0
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def ref_stats(images, tagger):
    pal = np.mean([palette_hist(Image.open(p)) for p in images], axis=0)
    edge = float(np.mean([edge_density(Image.open(p)) for p in images]))
    tags = [tagger.predict(p)["general"] for p in images]
    return {"palette": pal, "edge": edge, "tags": tags}


def score_ckpt(probe_images, ref, tagger, vocab):
    pal = np.mean([palette_hist(Image.open(p)) for p in probe_images], axis=0)
    edge = float(np.mean([edge_density(Image.open(p)) for p in probe_images]))
    tags = [tagger.predict(p)["general"] for p in probe_images]

    # each channel histogram sums to 1 over its bins, so the 3-channel L1 distance
    # is bounded by 6 - divide by 6 to land in 0..1
    s_pal = max(0.0, 1.0 - float(np.abs(pal - ref["palette"]).sum()) / 6.0)
    s_edge = 1.0 - min(1.0, abs(edge - ref["edge"]) / max(1e-6, ref["edge"]))
    a = tag_vector(tags, vocab)
    b = tag_vector(ref["tags"], vocab)
    s_tag = float(np.dot(a, b))  # both unit-normalised
    total = W_TAG * s_tag + W_PALETTE * s_pal + W_EDGE * s_edge
    return {"total": round(total, 4), "tag": round(s_tag, 4),
            "palette": round(s_pal, 4), "edge": round(s_edge, 4),
            "edge_value": round(edge, 4)}


# ------------------------------------------------------------------ 1. prep
def cmd_prep(args, tagger=None):
    name = args.name
    trigger = args.trigger or ("style_" + name)
    src = REFS / name
    imgs = sorted(p for p in src.glob("*.png") if not p.name.startswith("_"))
    if len(imgs) < 8:
        log("need at least 8 reference images, found %d in %s" % (len(imgs), src), name)
        return 1

    tagger = tagger or WDTagger()
    # No rating gate: every image in the set is trained on. The tagger is still
    # needed for captions, but its rating never excludes anything.
    kept = list(imgs)

    for p in kept:
        cap = tagger.caption(p, prefix=trigger)
        p.with_suffix(".txt").write_text(cap, encoding="utf-8")

    # Second pass - this is what makes a character LoRA reusable:
    #   * tags constant across the whole set ARE the character's identity, so they
    #     are dropped and the trigger absorbs them (otherwise the LoRA only works
    #     when you also type "white hair, red eyes, ..." every time)
    #   * tagger noise (vtuber / watermark style hallucinations) is dropped too
    # What remains is the variable part - pose, scene, framing, expression.
    NOISE = {"virtual_youtuber", "artist_name", "watermark", "signature", "twitter_username",
             "web_address", "dated", "sample_watermark", "copyright_name", "logo"}
    caps = {}
    for p in kept:
        tags = [t.strip() for t in p.with_suffix(".txt").read_text(encoding="utf-8").split(",")]
        caps[p] = [t for t in tags if t and t != trigger]

    freq = Counter()
    for tags in caps.values():
        for t in set(tags):
            freq[t] += 1
    n = len(kept)
    threshold = max(2, int(math.ceil(n * args.drop_frequent)))
    drop = {t for t, c in freq.items() if c >= threshold} | NOISE

    for p, tags in caps.items():
        body = [t for t in dict.fromkeys(tags) if t not in drop]
        p.with_suffix(".txt").write_text(", ".join([trigger] + body), encoding="utf-8")
    log("captions: dropped %d constant/noise tags, e.g. %s"
        % (len(drop), ", ".join(sorted(drop)[:10])), name)

    meta = {"name": name, "trigger": trigger, "kept": len(kept),
            "images": [p.name for p in kept],
            "dropped_tags": sorted(drop), "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    (src / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log("prep done: %d image(s) captioned, no rating gate, trigger=%s" % (len(kept), trigger), name)
    return 0


# ------------------------------------------------------------------ 2. train
def write_toml(name, image_dir, repeats, resolution):
    CONFIGS.mkdir(parents=True, exist_ok=True)
    path = CONFIGS / ("%s.toml" % name)
    # two TOML gotchas: resolution must be an array, and a Windows path inside a
    # basic string would be eaten by escape processing - use forward slashes.
    posix_dir = str(image_dir).replace("\\", "/")
    path.write_text(
        "[general]\n"
        "enable_bucket = true\n"
        "bucket_no_upscale = true\n"
        "resolution = [%d, %d]\n"
        'caption_extension = ".txt"\n'
        "shuffle_caption = false\n\n"
        "[[datasets]]\n"
        "batch_size = 1\n"
        "enable_bucket = true\n\n"
        "  [[datasets.subsets]]\n"
        '  image_dir = "%s"\n'
        "  num_repeats = %d\n" % (resolution, resolution, posix_dir, repeats),
        encoding="utf-8",
    )
    return path


def cmd_train(args):
    name = args.name
    base_name, base_path = resolve_base(args.base)
    src = REFS / name
    toml = write_toml(name, src, args.repeats, args.resolution)
    outdir = run_dir(name, base_name)
    outdir.mkdir(parents=True, exist_ok=True)

    if not VENV_PY.exists():
        log("trainer venv missing: %s" % VENV_PY, name)
        return 2
    for f in (base_path, QWEN3, VAE):
        if not f.exists():
            log("missing model file: %s" % f, name)
            return 2
    # The block count comes back from the checkpoint itself - networks/lora_anima.py
    # walks the live unet's modules instead of assuming a depth, so the 40-block
    # expansion needs no code change here. Logging it makes a wrong base obvious.
    log("base checkpoint: %s" % base_name, name)

    # GPU is a single 8GB resource: ComfyUI must let go before training starts
    c = Comfy()
    if c.ready():
        try:
            c.free(unload_models=True, free_memory=True)
            log("asked ComfyUI to unload models (freeing VRAM for training)", name)
            time.sleep(5)
        except Exception as e:
            log("free call failed (continuing): %s" % e, name)

    cmd = [
        str(VENV_PY), "-m", "accelerate.commands.launch",
        "--num_cpu_threads_per_process", "1", "anima_train_network.py",
        "--pretrained_model_name_or_path=%s" % base_path,
        "--qwen3=%s" % QWEN3,
        "--vae=%s" % VAE,
        "--dataset_config=%s" % toml,
        "--output_dir=%s" % outdir,
        "--output_name=%s" % name,
        "--save_model_as=safetensors",
        "--network_module=networks.lora_anima",
        "--network_dim=%d" % args.dim,
        "--network_alpha=%d" % args.alpha,
        # anima_train_network.py asserts if the text encoder is both trained and
        # cached; UNet-only + caching is the memory-cheap combination we want
        # (note: the repo's own example command omits this flag and fails).
        "--network_train_unet_only",
        "--learning_rate=%s" % args.lr,
        "--optimizer_type=AdamW8bit",
        "--lr_scheduler=cosine_with_restarts",
        "--lr_scheduler_num_cycles=1",
        "--lr_warmup_steps=%d" % max(10, args.steps // 20),
        "--timestep_sampling=sigmoid",
        # NOTE: --discrete_flow_shift is deliberately NOT passed. With
        # timestep_sampling=sigmoid the training log states
        # "discrete_flow_shift=1.0 (IGNORED for timestep_sampling='sigmoid')" - the
        # shift only takes effect for 'sigma' and 'shift' sampling.
        "--max_train_steps=%d" % args.steps,
        "--save_every_n_steps=%d" % args.save_every,
        "--mixed_precision=bf16",
        "--gradient_checkpointing",
        "--cache_latents",
        "--cache_text_encoder_outputs",
        # DataLoader worker processes. kohya defaults to 8, and with --cache_latents the
        # latent cache lives in the dataset object - so every worker gets its own copy.
        # Measured on this 15.8 GB machine: 8 workers of the 2.9B run held ~1 GB each and
        # left 0.1 GiB of RAM free, which turned a 2B run's 1.7 s/step into 43 s/step for
        # the 40-block model, because the driver then had nowhere to evict VRAM to.
        # 0 loads in the main process: no copies, and the frames are already cached.
        "--max_data_loader_n_workers=%d" % args.workers,
        # The image-only 2D Qwen-Image VAE is numerically equivalent for single
        # images, about 2x faster to encode/decode and peaks at ~1/3 of the VRAM;
        # the docs recommend it for latent caching. With it --vae_disable_cache is
        # a no-op, so it is not passed any more.
        "--qwen_image_vae_2d",
        "--vae_chunk_size=64",
        "--seed=42",
    ]
    # Keyed by the RUN directory, not just the style name: two architectures trained under
    # one style name used to append to a single file, which makes "what rate did this run
    # hold?" unanswerable and is exactly the shared-log trap that produced two wrong
    # conclusions in an earlier session.
    train_log = LOGS / ("train_%s.log" % outdir.name)
    log("training start: steps=%d dim=%d lr=%s workers=%d (log: %s)"
        % (args.steps, args.dim, args.lr, args.workers, train_log), name)
    logf = train_log.open("a", encoding="utf-8")
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(SD), stdout=logf, stderr=subprocess.STDOUT, text=True)
    logf.close()
    dt = time.time() - t0
    log("training finished rc=%d in %.1f min" % (proc.returncode, dt / 60), name)
    return 0 if proc.returncode == 0 else 3


# ------------------------------------------------------------------ 3. convert
def convert_ckpt(ckpt: Path, dst_name: str):
    dst = LORAS / dst_name
    cmd = [str(VENV_PY), "networks/convert_anima_lora_to_comfy.py", str(ckpt), str(dst)]
    r = subprocess.run(cmd, cwd=str(SD), capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        log("convert failed: %s" % (r.stdout[-400:] + r.stderr[-400:]))
        return None
    return dst


# ------------------------------------------------------------------ 4. probe
def cmd_probe(args):
    name = args.name
    base_name, _ = resolve_base(args.base)
    rundir = run_dir(name, base_name)
    meta = read_json(REFS / name / "_meta.json", {})
    trigger = meta.get("trigger") or ("style_" + name)
    ckpts = sorted(rundir.glob("*.safetensors"))
    if not ckpts:
        log("no checkpoints in %s" % rundir, name)
        return 2
    log("probing %d checkpoint(s) from %s on %s" % (len(ckpts), rundir.name, base_name), name)

    tagger = WDTagger()
    ref_imgs = sorted(REFS.glob("%s/[0-9]*.png" % name))
    ref = ref_stats(ref_imgs, tagger)
    vocab = {}
    for tags in ref["tags"]:
        for t in tags:
            vocab.setdefault(t, len(vocab))

    c = Comfy()
    if not c.ready():
        log("ComfyUI not reachable (needed for probing)", name)
        return 2

    results = {}
    tmp_made = []
    for ck in ckpts:
        stem = ck.stem
        lora_name = "_probe_%s_%s.safetensors" % (name, stem)
        if not convert_ckpt(ck, lora_name):
            continue
        tmp_made.append(LORAS / lora_name)
        outdir = PROBES / name / stem
        outdir.mkdir(parents=True, exist_ok=True)
        made = []
        for pid, tpl in PROBE_PROMPTS:
            # "model" pins the checkpoint: runner.build_workflow takes it over its own
            # pick_unet, so a 2.9B checkpoint is probed on 2.9B instead of being routed
            # back to the 28-block family just because the job carries a style LoRA.
            job = {"id": pid, "width": args.width, "height": args.height, "seed": 9000 + len(made),
                   "turbo": False, "steps": args.probe_steps, "cfg": 4.0, "model": base_name,
                   "prompt": tpl.format(t=trigger), "style_lora": lora_name,
                   "style_strength": args.strength, "prefix": "probe_%s_%s_%s" % (name, stem, pid)}
            wf = runner.build_workflow(job)
            try:
                qid = c.submit(wf)
                entry, _ = c.wait(qid)
                if c.status_str(entry) != "success":
                    log("probe %s/%s failed: %s" % (stem, pid, c.error_messages(entry)), name)
                    continue
                imgs = c.output_images(entry)
                if not imgs:
                    continue
                src = runner.RAW_OUT / imgs[0].get("subfolder", "") / imgs[0]["filename"]
                dst = outdir / ("%s.png" % pid)
                shutil.copy2(src, dst)
                made.append(dst)
            except Exception as e:
                log("probe %s/%s error: %s" % (stem, pid, e), name)
        if made:
            s = score_ckpt(made, ref, tagger, vocab)
            s["images"] = [p.name for p in made]
            results[stem] = s
            log("  %-22s total=%.4f (tag=%.3f pal=%.3f edge=%.3f)"
                % (stem, s["total"], s["tag"], s["palette"], s["edge"]), name)

    # keep only the winning temp file; drop the rest so models/loras stays clean
    best = max(results.items(), key=lambda kv: kv[1]["total"])[0] if results else None
    for p in tmp_made:
        keep = best and p.name == "_probe_%s_%s.safetensors" % (name, best)
        if not keep:
            p.unlink(missing_ok=True)
    summary = {"name": name, "trigger": trigger, "best": best, "scores": results,
               "base": base_name, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    (PROBES / name / "_score.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    log("probe done: best=%s" % best, name)
    return 0


# ------------------------------------------------------------------ 5. deploy
def cmd_deploy(args):
    name = args.name
    base_name, _ = resolve_base(args.base)
    rundir = run_dir(name, base_name)
    summary = read_json(PROBES / name / "_score.json", {})
    best = args.ckpt or summary.get("best")
    if not best:
        log("nothing to deploy (run probe first)", name)
        return 2
    src = rundir / ("%s.safetensors" % best)
    if not src.exists():
        cand = sorted(rundir.glob("*%s*.safetensors" % best))
        if not cand:
            log("checkpoint not found: %s" % src, name)
            return 2
        src = cand[0]

    final_name = "style_%s.safetensors" % name
    reg = read_json(REG, {})
    prev = reg.get(name) or {}
    # A style keeps one stable lora_file, so existing jobs, presets and style names never
    # have to be rewritten when the base changes. The adapter being replaced is moved
    # aside rather than deleted - it is the only copy that still matches the other
    # architecture, and the two cannot be swapped back and forth once one is gone.
    dst = LORAS / final_name
    prev_base = prev.get("base") or BASES["base"]
    if dst.exists() and prev_base != base_name:
        keep = LORAS / "_superseded"
        keep.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(keep / ("%s__%s" % (dst.stem, prev_base))))
        log("previous adapter (%s) moved to %s" % (prev_base, keep.name), name)

    if not convert_ckpt(src, final_name):
        return 3
    for p in LORAS.glob("_probe_%s_*.safetensors" % name):
        p.unlink(missing_ok=True)

    entry = dict(prev)          # keeps hand-written notes such as strength_note
    entry.update({
        "trigger": summary.get("trigger") or ("style_" + name),
        "lora_file": final_name,
        "base": base_name,
        "strength": args.strength,
        "checkpoint": best,
        "score": (summary.get("scores") or {}).get(best, {}).get("total"),
        "deployed": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    reg[name] = entry
    REG.parent.mkdir(parents=True, exist_ok=True)
    REG.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    log("deployed: models/loras/%s on %s (checkpoint %s, score %s)"
        % (final_name, base_name, best, reg[name]["score"]), name)
    log("use it: jobs take style_lora=%s and the trigger '%s' in the prompt"
        % (final_name, reg[name]["trigger"]), name)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--name", required=True)
        p.add_argument("--trigger", default=None)
        # Accepted by every step so `run` can carry one value through the chain; only
        # train/probe/deploy act on it (prep produces captions, which are base-agnostic).
        p.add_argument("--base", default=DEFAULT_BASE,
                       help="%s, or an exact filename in models/diffusion_models"
                            % " | ".join(sorted(BASES)))

    p1 = sub.add_parser("prep"); common(p1)
    p1.add_argument("--drop-frequent", type=float, default=0.8,
                    help="drop tags present in this share of images (character identity)")
    p1.set_defaults(func=None)
    p2 = sub.add_parser("train"); common(p2)
    p2.add_argument("--steps", type=int, default=1200)
    p2.add_argument("--save-every", type=int, default=150)
    p2.add_argument("--dim", type=int, default=32)
    p2.add_argument("--alpha", type=int, default=32)
    p2.add_argument("--lr", default="2e-5")
    p2.add_argument("--repeats", type=int, default=6)
    p2.add_argument("--resolution", type=int, default=768)
    p2.add_argument("--workers", type=int, default=0,
                    help="DataLoader worker processes; 0 keeps the cached latents in one "
                         "process (see the note in cmd_train), which is what fits this "
                         "machine's RAM")
    p2.set_defaults(func=cmd_train)
    p3 = sub.add_parser("probe"); common(p3)
    p3.add_argument("--width", type=int, default=832)
    p3.add_argument("--height", type=int, default=1216)
    p3.add_argument("--probe-steps", type=int, default=24)
    p3.add_argument("--strength", type=float, default=0.8)
    p3.set_defaults(func=cmd_probe)
    p4 = sub.add_parser("deploy"); common(p4)
    p4.add_argument("--ckpt", default=None)
    p4.add_argument("--strength", type=float, default=0.8)
    p4.set_defaults(func=cmd_deploy)
    p5 = sub.add_parser("run"); common(p5)
    # run chains prep -> train -> probe -> deploy, so it needs every flag those steps
    # take. --drop-frequent lived only on prep, which made `run` die with AttributeError
    # inside cmd_prep before doing anything - a chain that cannot be invoked as one
    # command is not a chain, and that is why this style was trained step by step.
    p5.add_argument("--drop-frequent", type=float, default=0.8,
                    help="drop tags present in this share of images (character identity)")
    p5.add_argument("--steps", type=int, default=1200)
    p5.add_argument("--save-every", type=int, default=150)
    p5.add_argument("--dim", type=int, default=32)
    p5.add_argument("--alpha", type=int, default=32)
    p5.add_argument("--lr", default="2e-5")
    p5.add_argument("--repeats", type=int, default=6)
    p5.add_argument("--resolution", type=int, default=768)
    p5.add_argument("--workers", type=int, default=0)
    p5.add_argument("--width", type=int, default=832)
    p5.add_argument("--height", type=int, default=1216)
    p5.add_argument("--probe-steps", type=int, default=24)
    p5.add_argument("--strength", type=float, default=0.8)
    p5.add_argument("--ckpt", default=None)
    p5.set_defaults(func=None)

    args = ap.parse_args()

    if args.cmd == "prep":
        return cmd_prep(args)
    if args.cmd == "train":
        return cmd_train(args)
    if args.cmd == "probe":
        return cmd_probe(args)
    if args.cmd == "deploy":
        return cmd_deploy(args)
    if args.cmd == "run":
        rc = cmd_prep(args)
        if rc:
            return rc
        rc = cmd_train(args)
        if rc:
            return rc
        rc = cmd_probe(args)
        if rc:
            return rc
        return cmd_deploy(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())