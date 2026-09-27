"""AnimaStudio runner: turn a job queue into rendered panels.

Design notes
------------
* render-only. QC runs as a separate pass so the CPU tagger never
  competes with the GPU renderer - this is the "time slicing" the studio is built on.
* resumable. Every finished job is recorded in state.json (atomic replace), so
  killing the process and restarting never re-renders finished panels.
* controllable. control.json supports pause / resume / stop / reroll:<id>.
  The agent writes intents there; it is never allowed to kill processes itself.

CLI:
  python runner.py                 # run all pending jobs
  python runner.py --limit 3       # render at most 3 panels this pass
  python runner.py --dry-run       # print the resolved plan, submit nothing
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from comfy import Comfy, ComfyError  # noqa: E402

ROOT = Path(r"D:\AnimaStudio")
JOBS = ROOT / "workspace" / "jobs" / "jobs.jsonl"
STATE = ROOT / "workspace" / "jobs" / "state.json"
CONTROL = ROOT / "workspace" / "jobs" / "control.json"
STYLE_REG = ROOT / "workspace" / "jobs" / "styles.json"
RAW_OUT = ROOT / "workspace" / "out" / "raw"
PANELS = ROOT / "workspace" / "out" / "panels"
LOGFILE = ROOT / "workspace" / "logs" / "runner.log"
TEMPLATE = HERE / "templates" / "anima_t2i.json"
UNET_DIR = ROOT / "models" / "diffusion_models"
LORAS_DIR = ROOT / "models" / "loras"
COMFY_INPUT = ROOT / "ComfyUI_windows_portable" / "ComfyUI" / "input"

# Optional adapters, both already on disk (install.ps1 places the pose one, the
# character-reference one comes from darask0/Anima-InContext-Character). The custom
# nodes that consume them live in ComfyUI\custom_nodes.
INCONTEXT_LORA = "anima-incontext-character.safetensors"   # reference-image character
POSE_LORA = "anima_pose_preview2.safetensors"              # pose control adapter
# A LoRA file is only half of these features: each also needs its ComfyUI node pack
# installed. A graph that names an uninstalled node dies at submit time with a
# node-type error, which reads like a rendering bug rather than a missing dependency,
# so both halves get checked before the graph is built.
INCONTEXT_NODES = ("AnimaRefEncode", "AnimaRefLatentBatch", "AnimaInContextApply")
POSE_NODES = ("AnimaPoseControl", "AnimaControlApply")

_MISSING_NODES = {}


def missing_nodes(names):
    """Cached ComfyUI node-type check. Never raises: an unreachable server cannot prove
    the nodes are absent, and the render path already fails loudly on its own."""
    key = tuple(names)
    if key not in _MISSING_NODES:
        try:
            _MISSING_NODES[key] = Comfy().missing_nodes(list(names))
        except Exception:
            _MISSING_NODES[key] = []
    return _MISSING_NODES[key]

# Anima ships three families: Base (unrefined - officially meant for *training*
# LoRAs, and "very plain and neutral" when used to render), Aesthetic (better
# default style and consistency) and Turbo (distilled, CFG 1 / 8-12 steps).
# Drafts and finals therefore prefer the tuned checkpoints and fall back to whatever
# is actually installed, so a base-only install keeps working unchanged.
UNET_FINAL_CANDIDATES = ("anima-aesthetic-v1.1.safetensors",
                         "anima-aesthetic-v1.0.safetensors")
UNET_DRAFT_CANDIDATES = ("anima-turbo-v1.1.safetensors",
                         "anima-turbo-v1.0.safetensors")
UNET_FALLBACK = "anima-base-v1.0.safetensors"
# The 40-block layer expansion of the base. Which checkpoint a job gets is decided per
# adapter in pick_unet: an adapter is only valid on the architecture it was trained on.
ANIMA29 = "anima-2.9b-preview-v1.safetensors"

FINAL_STEPS = 40   # official range is 30-50 steps; 40 is the better default for finals
FINAL_CFG = 4.0
# Anima's tested envelope is roughly 1536x1536. A refine pass past that is where 8GB
# starts thrashing, so an over-large request is logged rather than silently attempted.
HIRES_MP_WARN = 2.4

# No rating token is forced into the positive prompt: the old prefix ended in
# "safe, ", which biased every panel towards one rating. Content is whatever the
# job's own prompt asks for.
POS_PREFIX = "masterpiece, best quality, score_7, "
# Aesthetic is fine-tuned with the quality tags stripped out of its captions, and the
# model author recommends NOT using score_* on it ("it can push it too hard into slop").
POS_PREFIX_AESTHETIC = "masterpiece, best quality, "
# Anti-artifact block: anime models trained on booru data love to invent corner
# logos, pseudo-text, signatures and dates.
NEG_ARTIFACT = (
    "artist name, blurry, jpeg artifacts, chromatic aberration, "
    "watermark, signature, logo, text, english text, japanese text, twitter username, "
    "web address, copyright name, dated, "
    # Censorship. An anime model asked for an intimate scene will sometimes draw the
    # mosaic bar or the black strip unprompted - the style LoRA here was trained on
    # material whose own tag list contains `censored` and `mosaic_censoring`, so it has
    # learned the look. Banning it by name is the only lever the negative prompt has.
    "censored, mosaic censoring, bar censor, "
    # The cut-out halo. Anima-2.9B draws a white outline around a character who fills the
    # frame with strong sheet tags - a sticker look - and nothing asked for it: the prompt
    # carries no outline/border tag and no negative banned one. Measured on the same seed,
    # naming it here removes the halo entirely and the figure sits in the scene properly.
    # It cost nothing and would otherwise have shipped on every page of a 40-page book.
    "outline, white outline, border, sticker, cutout, die-cut, "
    # Hands. The polished Anima workflow the user supplied weights this term, which is
    # also the defect measured by hand on this project's own final renders (a merged,
    # fingerless hand on p001f), so it is weighted here too.
    "(polydactyl:1.5), extra digits, fused fingers, missing fingers"
)
# Bubbles are banned by default (the model draws pseudo-glyphs inside them), but a
# page that compose.py is going to letter needs one blank bubble to write into.
NEG_BUBBLE = ", speech bubble"
NEG_DEFAULT = "worst quality, low quality, score_1, score_2, score_3, " + NEG_ARTIFACT
NEG_DEFAULT_AESTHETIC = "worst quality, low quality, " + NEG_ARTIFACT


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    with LOGFILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_jobs():
    if not JOBS.exists():
        return []
    out = []
    for i, line in enumerate(JOBS.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            job = json.loads(line)
        except Exception as e:
            log("SKIP malformed job line %d: %s" % (i, e))
            continue
        job.setdefault("id", "p%03d" % i)
        out.append(job)
    return out


def safetensors_complete(path):
    """True only when the whole tensor block of a .safetensors is on disk.

    Downloads land in place, so a half-finished checkpoint *exists* - and would
    otherwise be picked as the checkpoint and then fail deep inside ComfyUI with a
    confusing error. The header declares every tensor's byte range, so the file size
    can be checked against it without knowing any expected size in advance.
    """
    try:
        with Path(path).open("rb") as f:
            n = int.from_bytes(f.read(8), "little")
            if not 0 < n < 100_000_000:
                return False
            header = json.loads(f.read(n).decode("utf-8"))
        end = 0
        for meta in header.values():
            if isinstance(meta, dict) and "data_offsets" in meta:
                end = max(end, int(meta["data_offsets"][1]))
        return Path(path).stat().st_size >= 8 + n + end
    except Exception:
        return False


_STYLE_CACHE = (None, None)   # (mtime, registry)


def style_registry():
    """workspace/jobs/styles.json, reloaded when the file changes.

    This sits on the render path, so it is cached; the mtime check stops a long-running
    orchestrate pass from routing on a registry a concurrent deploy has replaced.
    """
    # Without `global`, the assignment below makes _STYLE_CACHE local to this function,
    # so the read on the next line raises UnboundLocalError. That crash only ever fires
    # on the FINAL render path (drafts never call pick_unet with a job) - which is how a
    # 40-page run got through all 40 drafts and died the instant finals began.
    global _STYLE_CACHE
    try:
        mtime = STYLE_REG.stat().st_mtime
    except OSError:
        return {}
    if _STYLE_CACHE[0] != mtime:
        try:
            _STYLE_CACHE = (mtime, json.loads(STYLE_REG.read_text(encoding="utf-8")))
        except Exception:
            return {}
    return _STYLE_CACHE[1] or {}


def adapter_family(job):
    """Which DiT family this job's adapters require: "29", "2b", or None for a free choice.

    Anima-2.9B is a 40-block layer expansion of the 28-block base - the author's
    expand_manifest lists insertions at blocks 2,5,8,...,36, so source block 20 now sits
    at block 30. Those blocks still exist on the 40-block model, so an adapter trained on
    one architecture LOADS WITHOUT ERROR on the other and lands on the wrong layers: a
    silent degradation, not a crash. The checkpoint therefore follows the adapter.

    ref_images and pose_image are third-party 28-block adapters that cannot be retrained
    from here, so they pin the 2B family permanently. A style LoRA follows the base that
    style_train.py recorded for it; a style with no recorded base predates the field and
    is 2B.
    """
    if job.get("ref_images") or job.get("pose_image"):
        return "2b"
    lora = job.get("style_lora")
    if not lora:
        return None
    for rec in style_registry().values():
        if rec.get("lora_file") == lora:
            return "29" if "2.9b" in str(rec.get("base") or "") else "2b"
    return "2b"


def pick_unet(candidates, job=None):
    """First complete checkpoint from a preference list, else the base model.

    A job with no adapter gets the 40-block expansion; one whose adapters were trained on
    the 28-block family gets that family instead. An adapter that needs the expansion
    while the file is missing is a loud warning, not a silent swap: the fallback still
    renders, so a wrong-looking panel could pass QC unnoticed.
    """
    if job is not None:
        fam = adapter_family(job)
        if fam != "2b":
            if safetensors_complete(UNET_DIR / ANIMA29):
                return ANIMA29
            if fam == "29":
                log("WARNING %s: style LoRA was trained on the 40-block expansion but %s "
                    "is missing or incomplete - falling back to the 28-block family, so "
                    "the adapter will mis-map" % (job.get("id"), ANIMA29))
    for name in candidates:
        if safetensors_complete(UNET_DIR / name):
            return name
    return UNET_FALLBACK


def prompt_prefix(unet_name):
    return POS_PREFIX_AESTHETIC if unet_name.startswith("anima-aesthetic") else POS_PREFIX


def negative_default(unet_name, allow_bubble=False):
    base = NEG_DEFAULT_AESTHETIC if unet_name.startswith("anima-aesthetic") else NEG_DEFAULT
    return base if allow_bubble else base + NEG_BUBBLE


def stage_input_image(path):
    """Copy an image into ComfyUI's input dir and return the name LoadImage wants.

    LoadImage only accepts a name relative to that directory, so a reference photo
    living anywhere on disk has to be staged first. Re-copies only when the source
    is newer, so a batch of panels shares one copy.
    """
    src = Path(path)
    if not src.is_file():
        return None
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    dst = COMFY_INPUT / src.name
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        shutil.copy2(src, dst)
    return dst.name


def build_workflow(job):
    wf = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    wf["30"]["inputs"]["width"] = int(job.get("width", 1024))
    wf["30"]["inputs"]["height"] = int(job.get("height", 1024))

    sampler = wf["40"]["inputs"]
    sampler["seed"] = int(job.get("seed", 0))
    # er_sde is the author's default sampler (neutral style, flat colours, sharp
    # lines); sgm_uniform is the scheduler the Anima-2.9B author uses day to day.
    sampler["sampler_name"] = job.get("sampler", "er_sde")
    sampler["scheduler"] = job.get("scheduler", "sgm_uniform")
    sampler["denoise"] = float(job.get("denoise", 1.0))

    ref_images = [p for p in (job.get("ref_images") or []) if p]
    pose_image = job.get("pose_image")
    draft_unet = pick_unet(UNET_DRAFT_CANDIDATES)

    if job.get("turbo") and draft_unet != UNET_FALLBACK:
        # dedicated distilled checkpoint: it needs no LoRA, and CFG must stay at 1
        wf["1"]["inputs"]["unet_name"] = draft_unet
        sampler["steps"] = int(job.get("steps", 10))
        sampler["cfg"] = float(job.get("cfg", 1.0))
    elif job.get("turbo"):
        # base-only install: the old path, base model + turbo LoRA on node 10
        sampler["steps"] = int(job.get("steps", 10))
        sampler["cfg"] = float(job.get("cfg", 1.0))
        wf["10"]["inputs"]["lora_name"] = job.get("turbo_lora",
                                                  "anima-turbo-lora-v0.2.safetensors")
        wf["10"]["inputs"]["strength_model"] = float(job.get("turbo_strength", 1.0))
        sampler["model"] = ["10", 0]
    else:
        # The character-reference LoRA is trained against the unrefined base model, so
        # that path stays on base unless the job names a checkpoint itself.
        if ref_images and not job.get("model"):
            wf["1"]["inputs"]["unet_name"] = UNET_FALLBACK
        else:
            wf["1"]["inputs"]["unet_name"] = job.get("model") or pick_unet(UNET_FINAL_CANDIDATES, job)
        sampler["steps"] = int(job.get("steps", FINAL_STEPS))
        sampler["cfg"] = float(job.get("cfg", FINAL_CFG))
        if job.get("style_lora"):
            # The checkpoint was already chosen above by pick_unet from the adapter's
            # recorded base: a 2B style rides on Aesthetic, a 2.9B style stays on the
            # 40-block expansion it was trained against. Re-run verify_style.py after
            # switching either, the best strength can shift.
            wf["10"]["inputs"]["lora_name"] = job["style_lora"]
            wf["10"]["inputs"]["strength_model"] = float(job.get("style_strength", 0.8))
            sampler["model"] = ["10", 0]
        # with no LoRA node 10 still exists but goes unused (KSampler reads node 1)

    # ---- optional model-side adapters ------------------------------------------
    # Each stage chains onto whatever the KSampler currently reads, keeping the order
    # base -> style LoRA -> character reference -> pose control.
    model_src = list(sampler["model"])

    if ref_images and not safetensors_complete(LORAS_DIR / INCONTEXT_LORA):
        log("WARNING %s: character reference requested but %s is missing or still "
            "downloading - rendering without it" % (job["id"], INCONTEXT_LORA))
        ref_images = []
    if ref_images:
        gone = missing_nodes(INCONTEXT_NODES)
        if gone:
            log("WARNING %s: character reference needs the Anima InContext node pack, "
                "which this ComfyUI does not have (%s) - rendering without a reference. "
                "The LoRA file was only half of it." % (job["id"], ", ".join(gone)))
            ref_images = []
    if ref_images:
        names = [n for n in (stage_input_image(p) for p in ref_images) if n]
        if names:
            wf["70"] = {"class_type": "LoadImage", "inputs": {"image": names[0]}}
            wf["71"] = {"class_type": "AnimaRefEncode",
                        "inputs": {"vae": ["3", 0], "image": ["70", 0],
                                   "target_width": wf["30"]["inputs"]["width"],
                                   "target_height": wf["30"]["inputs"]["height"]}}
            ref_latent = ["71", 0]
            if len(names) > 1:
                wf["72"] = {"class_type": "LoadImage", "inputs": {"image": names[1]}}
                wf["73"] = {"class_type": "AnimaRefEncode",
                            "inputs": {"vae": ["3", 0], "image": ["72", 0],
                                       "target_width": wf["30"]["inputs"]["width"],
                                       "target_height": wf["30"]["inputs"]["height"]}}
                wf["74"] = {"class_type": "AnimaRefLatentBatch",
                            "inputs": {"ref_latent_1": ref_latent,
                                       "ref_latent_2": ["73", 0], "fit_mode": "pad"}}
                ref_latent = ["74", 0]
            wf["75"] = {"class_type": "LoraLoaderModelOnly",
                        "inputs": {"model": model_src, "lora_name": INCONTEXT_LORA,
                                   "strength_model": float(job.get("incontext_lora_strength", 1.0))}}
            wf["76"] = {"class_type": "AnimaInContextApply",
                        "inputs": {"model": ["75", 0], "ref_latent": ref_latent,
                                   "strength": float(job.get("incontext_strength", 1.0)),
                                   "start_percent": 0.0, "end_percent": 1.0,
                                   "cond_only": True, "fit_mode": "pad",
                                   "ref_timestep": 0.0}}
            model_src = ["76", 0]

    if pose_image and not safetensors_complete(LORAS_DIR / POSE_LORA):
        log("WARNING %s: pose control requested but %s is missing or incomplete - "
            "rendering without it" % (job["id"], POSE_LORA))
        pose_image = None
    if pose_image:
        gone = missing_nodes(POSE_NODES)
        if gone:
            log("WARNING %s: pose control needs the Anima pose node pack, which this "
                "ComfyUI does not have (%s) - rendering without it"
                % (job["id"], ", ".join(gone)))
            pose_image = None
    if pose_image:
        name = stage_input_image(pose_image)
        if name:
            wf["80"] = {"class_type": "LoadImage", "inputs": {"image": name}}
            # AnimaPoseControl redetects the skeleton from the photo, so no pose_json
            # and no hand-editing of keypoints is needed for unattended runs.
            wf["81"] = {"class_type": "AnimaPoseControl",
                        "inputs": {"style": job.get("pose_style", "R0_thin"),
                                   "hands": True, "face": True, "feet": True,
                                   "redetect": True,
                                   "resolution": int(job.get("pose_resolution", 768)),
                                   "pose_json": "", "image": ["80", 0]}}
            wf["82"] = {"class_type": "VAEEncode",
                        "inputs": {"pixels": ["81", 0], "vae": ["3", 0]}}
            wf["83"] = {"class_type": "AnimaControlApply",
                        "inputs": {"model": model_src, "control_latent": ["82", 0],
                                   "control_embedder_path": POSE_LORA,
                                   "strength": float(job.get("pose_strength", 0.8))}}
            model_src = ["83", 0]

    sampler["model"] = model_src

    # ---- optional refine pass (the community "2k" workflow shape) ---------------
    # Render once, upscale the LATENT, then run a second sampler at low denoise. Only
    # the second pass ever runs at the larger size, which is what keeps this affordable
    # on 8GB - upscaling pixels and re-encoding would pay for the big size twice.
    # The community reference drops its control adapters for pass 2; we keep the whole
    # chain instead, because dropping the in-context reference would let the character
    # drift in exactly the pass that is supposed to add the detail.
    hires = float(job.get("hires") or 0)
    if hires > 1.0:
        hi_w = int(wf["30"]["inputs"]["width"] * hires)
        hi_h = int(wf["30"]["inputs"]["height"] * hires)
        if hi_w * hi_h / 1e6 > HIRES_MP_WARN:
            log("WARNING %s: hires x%.2f -> %dx%d (%.2f MP) is past Anima's documented "
                "1536x1536 envelope; expect it to be slow or to run out of VRAM"
                % (job["id"], hires, hi_w, hi_h, hi_w * hi_h / 1e6))
        wf["90"] = {"class_type": "LatentUpscaleBy",
                    "inputs": {"samples": ["40", 0],
                               "upscale_method": job.get("hires_method", "nearest-exact"),
                               "scale_by": hires}}
        wf["91"] = {"class_type": "KSampler",
                    "inputs": {"model": sampler["model"], "positive": ["20", 0],
                               "negative": ["21", 0], "latent_image": ["90", 0],
                               "seed": sampler["seed"], "steps": sampler["steps"],
                               "cfg": sampler["cfg"],
                               "sampler_name": sampler["sampler_name"],
                               "scheduler": sampler["scheduler"],
                               "denoise": float(job.get("hires_denoise", 0.25))}}
        # the finished image is decoded from the refined latent, not from pass 1
        wf["50"]["inputs"]["samples"] = ["91", 0]

    unet = wf["1"]["inputs"]["unet_name"]
    prompt = job.get("prompt") or ""
    if not prompt.startswith("masterpiece"):
        prompt = prompt_prefix(unet) + prompt
    wf["20"]["inputs"]["text"] = prompt
    wf["21"]["inputs"]["text"] = job.get("negative") or negative_default(
        unet, bool(job.get("allow_bubble")))

    wf["60"]["inputs"]["filename_prefix"] = job.get("prefix") or ("panel_" + job["id"])
    return wf


def collect_output(entry, job, state):
    imgs = Comfy.output_images(entry)
    if not imgs:
        return None
    img = imgs[0]
    src = RAW_OUT / img.get("subfolder", "") / img["filename"]
    if not src.exists():
        return None
    PANELS.mkdir(parents=True, exist_ok=True)
    dst = PANELS / ("%s.png" % job["id"])
    shutil.copy2(src, dst)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-attempts", type=int, default=3)
    args = ap.parse_args()

    jobs = load_jobs()
    state = read_json(STATE, {})
    if not jobs:
        log("no jobs found in %s" % JOBS)
        return 1

    c = Comfy()
    if not args.dry_run and not c.ready():
        log("ComfyUI not reachable at %s - start it first (start_comfyui.bat)" % c.base)
        return 2

    done = 0
    for job in jobs:
        jid = job["id"]
        rec = state.get(jid, {})
        if rec.get("status") in ("done", "skipped"):
            continue
        if args.limit and done >= args.limit:
            log("limit reached (%d), stopping this pass" % args.limit)
            break

        ctl = read_json(CONTROL, {})
        mode = ctl.get("mode", "run")
        if mode == "stop":
            log("control: stop requested, exiting")
            break
        if mode == "pause":
            log("control: paused, exiting this pass (state kept)")
            break

        wf = build_workflow(job)
        if args.dry_run:
            log("DRY %s %dx%d steps=%s cfg=%s seed=%s model=%s sampler=%s/%s hires=%s "
                "turbo=%s style=%s"
                % (jid, wf["30"]["inputs"]["width"], wf["30"]["inputs"]["height"],
                   wf["40"]["inputs"]["steps"], wf["40"]["inputs"]["cfg"],
                   wf["40"]["inputs"]["seed"], wf["1"]["inputs"]["unet_name"],
                   wf["40"]["inputs"]["sampler_name"], wf["40"]["inputs"]["scheduler"],
                   job.get("hires") or "-",
                   bool(job.get("turbo")), job.get("style_lora")))
            done += 1
            continue

        attempts = int(rec.get("attempts", 0))
        while attempts < args.max_attempts:
            attempts += 1
            t0 = time.time()
            try:
                pid = c.submit(wf)
                entry, _min_free = c.wait(pid)
                st = Comfy.status_str(entry)
                if st != "success":
                    for m in Comfy.error_messages(entry):
                        log("  server error: %s" % m)
                    raise ComfyError("status=%s" % st)
                out = collect_output(entry, job, state)
                if not out:
                    raise ComfyError("no output image found")
                dt = time.time() - t0
                state[jid] = {
                    "status": "done",
                    "file": str(out),
                    "attempts": attempts,
                    "seconds": round(dt, 1),
                    "prompt_id": pid,
                    "seed": wf["40"]["inputs"]["seed"],
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                write_json_atomic(STATE, state)
                log("DONE %s -> %s (%.1fs, attempt %d)" % (jid, out.name, dt, attempts))
                done += 1
                break
            except (ComfyError, Exception) as e:
                log("FAIL %s attempt %d/%d: %s" % (jid, attempts, args.max_attempts, e))
                state[jid] = {"status": "failed", "attempts": attempts,
                              "error": str(e)[:500], "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                write_json_atomic(STATE, state)
                if attempts >= args.max_attempts:
                    log("GIVEUP %s after %d attempts" % (jid, attempts))
                else:
                    time.sleep(5)

    log("pass finished: %d rendered" % done)
    return 0


if __name__ == "__main__":
    sys.exit(main())