"""AnimaStudio orchestrator: one command for the whole unattended chain.

  services up -> agent plans -> draft render -> QC -> agent review
              -> finalize -> final render -> compose page -> run report

Every stage is a subprocess of the studio's own python, so a failure is visible
in the run log rather than silently swallowed. Services that are down are
started headless first (no windows popping up on the desktop).

CLI (normally called through batch_run.bat):
  # story file you wrote yourself
  python orchestrate.py --story workspace/jobs/story.txt --panels 8 --title 秘密

  # fully autonomous: train on your own images, invent a story, draw and compose it
  python orchestrate.py --train-refs D:/my_refs --train-name my_char --auto-story \
      --sheet workspace/jobs/my_char_sheet.txt --panels 4 --title 我的角色

  # reuse an already deployed style, with a premise instead of a story file
  python orchestrate.py --style orig_student --premise "转学第一天遇到一只猫" --panels 4
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from comfy import Comfy  # noqa: E402

ROOT = Path(r"D:\AnimaStudio")
PY = ROOT / "ComfyUI_windows_portable" / "python_embeded" / "python.exe"
PIPE = ROOT / "pipeline"
LOGS = ROOT / "workspace" / "logs"
JOBS = ROOT / "workspace" / "jobs" / "jobs.jsonl"
STATE = ROOT / "workspace" / "jobs" / "state.json"
PANELS = ROOT / "workspace" / "out" / "panels"
PAGES = ROOT / "workspace" / "out" / "pages"
TOOLS = ROOT / "tools"

COMFY_URL = "http://127.0.0.1:8188/system_stats"
AGENT_URL = "http://127.0.0.1:8080/v1/models"

RUN_LOG = None


def log(msg):
    line = "[run %s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    if RUN_LOG:
        with RUN_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def http_ok(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_service(name, url, launcher, wait_s=180):
    if http_ok(url):
        log("%s already up" % name)
        return True
    log("%s down -> starting headless" % name)
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(TOOLS / launcher)],
        cwd=str(ROOT),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if http_ok(url):
            log("%s ready after %.0fs" % (name, time.time() - t0))
            return True
        time.sleep(3)
    log("%s did NOT come up within %ds" % (name, wait_s))
    return False


def read_story_title(path, max_len=16):
    """The story file may carry a '# title' first line; use it for the page header."""
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("#"):
                t = s.lstrip("#").replace("标题：", "").replace("标题:", "").strip()
                return t if 0 < len(t) <= max_len else None
    except Exception:
        pass
    return None


def stage_reference_set(src, name, refs_root):
    """Copy a hand-made reference set into workspace/refs/<name>, normalising to PNG.

    The trainer only reads PNG, so anything else (jpg/webp/bmp) is converted on
    the way in. Images already in place are left alone.
    """
    from PIL import Image
    dst = refs_root / name
    dst.mkdir(parents=True, exist_ok=True)
    exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    staged = 0
    for p in sorted(Path(src).iterdir()):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        target = dst / p.name
        if p.resolve() == target.resolve() or target.exists():
            continue
        if p.suffix.lower() == ".png":
            shutil.copy2(p, target)
        else:
            Image.open(p).convert("RGB").save(dst / (p.stem + ".png"))
        staged += 1
    return dst, staged


def beat_key(jid):
    """The page an id belongs to, with its 'final' and 'reroll' markers stripped.

    Ids run p008 (draft) -> p008f (final) -> p008r1f (the judge's second attempt at that
    final). Kept in step with agent.beat_key, which the judge uses for the same reason;
    orchestrate drives agent.py as a subprocess rather than importing it, so this small
    pure function is duplicated rather than coupling the two.
    """
    s = str(jid)
    if not s.endswith("f"):
        return s
    s = s[:-1]
    i = s.rfind("r")
    if i > 0 and s[i + 1:].isdigit():
        s = s[:i]
    return s


def publish_pages(pre_ids, t_all, letter=True):
    """Page mode: a finished render already IS a page.

    Layout stays the model's job - pages are never re-composited - but the dialogue is
    real Chinese type, so compose.py writes each line into the blank bubble the prompt
    asked for. N renders really are N pages.

    Prefers this run's final renders, falls back to this run's drafts so that
    --skip-final still produces a viewable page.
    """
    fresh = [j for j in load_jobs() if str(j.get("id")) not in pre_ids]
    publish = ([j for j in fresh if str(j.get("id", "")).endswith("f")]
               or [j for j in fresh if j.get("turbo")])
    if not publish:
        log("no finished pages produced")
        return 5
    # One image per beat. Without this, a judge reroll would publish the page twice: once
    # as <beat>f and once as <beat>r1f. Later entries win, and a reroll is always queued
    # after the final it replaces, so the reroll is what ships.
    keep = {}
    for j in publish:
        keep[beat_key(j["id"])] = j
    publish = list(keep.values())
    PAGES.mkdir(parents=True, exist_ok=True)
    made = []
    for j in publish:
        src = PANELS / ("%s.png" % j["id"])
        if not src.exists():
            continue
        dst = PAGES / ("page_%s.png" % j["id"])
        line = (j.get("dialogue") or "").strip()
        if letter and line:
            stage("letter %s" % j["id"],
                  [PIPE / "compose.py", "--page", src, "--dialogue", line, "--out", dst],
                  allow_fail=True)
            if not dst.exists():
                shutil.copy2(src, dst)   # keep the page even if lettering failed
        else:
            shutil.copy2(src, dst)
        made.append(dst.name)
    log("page mode: %d page image(s) -> %s (model layout, code-lettered dialogue)"
        % (len(made), PAGES))
    for m in made:
        log("  %s" % m)
    log("ALL DONE in %.1f min | pages=%s" % ((time.time() - t_all) / 60, ", ".join(made)))
    log("run log: %s" % RUN_LOG)
    return 0


# Must match the threshold in tools\start_agent_headless.ps1: below it the launcher picks
# the CPU build, so yielding "too early" is not a slowdown, it is a silent 9x.
AGENT_NEEDS_MIB = 6500


def gpu_free_mib():
    """Free VRAM in MiB, read from nvidia-smi - the SAME source the launcher uses.

    ComfyUI's /system_stats reports its own allocator's view, and the two disagree badly:
    measured 6.3 GiB "free" by ComfyUI's count while nvidia-smi showed 184 MiB, because
    torch keeps its reserved blocks after the models are unloaded. Polling the wrong source
    made comfy_yield announce "enough" while the launcher - which samples nvidia-smi -
    then chose the CPU build and ran every LLM stage 9x slow. One source, or the handoff
    reports a number nobody acts on.
    """
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


def comfy_yield(timeout=180):
    """Hand the GPU to the agent before a stage that needs it.

    ComfyUI keeps the Anima weights resident until told otherwise, and the 9B plus its
    vision projector want ~6.3 GiB of a 7.96 GiB card, so the two can never hold it at
    once. /free drops the models; ComfyUI reloads them on the next prompt.

    /free returns before the memory is actually back. Measured: a fixed 2 s sleep left the
    card reading 1.5 GiB free, the launcher sampled that once, chose the CPU build, and
    every LLM stage ran 9x slow with nothing but one line in the log to show for it. So
    wait for the number the launcher is going to look at, rather than for a fixed delay.

    The timeout is 180s, not 60s. With --enable-dynamic-vram (which is what makes a 2.9B
    hires pass fit at all) ComfyUI kept the model resident past a 61s window - measured
    1.8 -> 2.0 GiB free while the agent was starting - so the launcher correctly chose the
    CPU build and the story stage took 93s instead of 13.8s. The release does happen, it
    just takes longer than a minute under dynamic VRAM.
    """
    # If the brain is already serving, it already holds the card and there is nothing to
    # yield for. Without this guard the check measures free VRAM against a GPU the agent
    # itself is occupying, so every LLM stage after the first logged "NOT enough, agent
    # will run on CPU" - true of the number, false about the situation, and it buried the
    # one case that actually matters (the agent being down and needing to start).
    if http_ok(AGENT_URL):
        return

    c = Comfy()
    before = gpu_free_mib()
    try:
        c.free(unload_models=True, free_memory=True)
    except Exception as e:
        log("ComfyUI /free failed, continuing anyway: %s" % e)
        return

    t0 = time.time()
    seen = before
    while time.time() - t0 < timeout:
        time.sleep(1.5)
        cur = gpu_free_mib()
        if cur is None:
            break
        seen = cur
        if cur >= AGENT_NEEDS_MIB:
            break
    if before is not None and seen is not None:
        verdict = "enough" if seen >= AGENT_NEEDS_MIB else "NOT enough, agent will run on CPU"
        log("GPU -> agent (ComfyUI released: %.1f -> %.1f GiB free in %.0fs - %s)"
            % (before / 1024, seen / 1024, time.time() - t0, verdict))
    else:
        log("GPU -> agent (ComfyUI released its models)")


def agent_release():
    """Hand the GPU back to the renderer, by stopping the agent.

    llama.cpp's server has no unload endpoint: the weights stay in VRAM for the life of
    the process, so releasing the card means ending it. It comes back in ~10s while the
    GGUF is still in page cache, which is small next to a render stage.
    """
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(TOOLS / "stop_agent.ps1")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        log("GPU -> renderer (%s)" % ((r.stdout or "").strip() or "no output"))
    except Exception as e:
        log("stop_agent.ps1 failed, continuing anyway: %s" % e)


def agent_mode():
    """The launcher's own account of which build it started.

    It cannot be read through a pipe: the server the launcher spawns inherits the handle,
    so a capturing caller blocks until that server exits (measured - subprocess.run with
    capture_output hung, Popen without it returned in 0.01s). The launcher appends its
    decision to a log instead, and this reads the last line back, which is how the run
    log ends up stating plainly whether the agent is on the GPU or the CPU.
    """
    try:
        lines = (LOGS / "agent-launch.log").read_text(encoding="utf-8").strip().splitlines()
        return lines[-1].strip() if lines else ""
    except Exception:
        return ""


def stage(title, args, allow_fail=False, needs=None):
    """Run one pipeline step.

    `needs` is the stage's claim on the single GPU this machine has. "llm" frees ComfyUI
    first and starts the brain if it is down; "gpu" stops the brain so the renderer gets
    the whole card. Declaring it per stage - rather than hand-placing handoff calls
    between stages - is what keeps the two halves from drifting apart as stages change.
    """
    if needs == "llm":
        comfy_yield()
        was_up = http_ok(AGENT_URL)
        if not ensure_service("agent LLM", AGENT_URL, "start_agent_headless.ps1"):
            log("agent LLM unavailable - %s cannot run" % title)
            return False
        if not was_up:
            log("agent: %s" % agent_mode())
    elif needs == "gpu":
        agent_release()

    log("--- %s ---" % title)
    t0 = time.time()
    proc = subprocess.run([str(PY)] + [str(a) for a in args], cwd=str(PIPE),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        log("  " + line)
    dt = time.time() - t0
    if proc.returncode != 0 and not allow_fail:
        log("%s FAILED rc=%d (%.1fs)" % (title, proc.returncode, dt))
        return False
    log("%s ok (%.1fs)" % (title, dt))
    return True


def load_jobs():
    out = []
    if JOBS.exists():
        for line in JOBS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def main():
    global RUN_LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--story", default=str(ROOT / "workspace" / "jobs" / "story.txt"))
    ap.add_argument("--panels", type=int, default=8)
    ap.add_argument("--title", default=None)
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--rows", type=int, default=4)
    ap.add_argument("--max-reroll", type=int, default=2)
    ap.add_argument("--skip-final", action="store_true", help="stop after drafts + QC")
    ap.add_argument("--style", default=None,
                    help="registered style name: the whole run uses that trained LoRA")
    ap.add_argument("--sheet", default=None,
                    help="pinned character sheet file: the model reuses it instead of inventing one")
    ap.add_argument("--ref-img", action="append", default=None,
                    help="character reference image (repeatable, max 2 used): attached to "
                         "every panel through the Anima In-Context nodes")
    ap.add_argument("--ref-strength", type=float, default=1.0,
                    help="attention pull toward --ref-img (1.0 neutral, 1.2-1.5 if "
                         "identity drifts)")
    ap.add_argument("--auto-preset", type=int, default=0, metavar="N",
                    help="let the model pick up to N presets for this story from a "
                         "shortlist retrieved off the story text (0 = off). Picks are "
                         "validated against the library, so a bad pick degrades to no "
                         "preset. Only non-adult presets are eligible unless "
                         "--auto-preset-adult is given")
    ap.add_argument("--auto-preset-adult", action="store_true",
                    help="also let --auto-preset draw from the adult presets. Opt-in on "
                         "purpose: the tone of a run is the operator's call, not a guess")
    ap.add_argument("--adult", action="store_true",
                    help="the premise is adult material: the story beats must carry the "
                         "physical escalation (not only mood) and the planner must render "
                         "them as such. Off by default, and that default matters: with it "
                         "off, an explicit premise came back as 40 beats of atmosphere "
                         "because nothing ever asked for more")
    ap.add_argument("--auto-artist", type=int, default=0, metavar="N",
                    help="let the model pick up to N @artists for this story from a menu "
                         "of name + style keywords (0 = off). Needs the style index built "
                         "by: python pipeline\\build_artist_styles.py --limit 100")
    ap.add_argument("--hires", type=float, default=None, metavar="SCALE",
                    help="add a latent-upscale refine pass to the final pages, e.g. 1.5 "
                         "(1024x1024 -> 1536x1536). Drafts stay fast; only finals pay")
    ap.add_argument("--hires-denoise", type=float, default=0.25,
                    help="denoise for the refine pass (0.40 tended to re-draw the hands on "
                         "this checkpoint; 0.25 kept them intact)")
    ap.add_argument("--hires-method", default="nearest-exact",
                    choices=["nearest-exact", "bislerp", "bilinear", "bicubic", "area"],
                    help="latent upscale used by the refine pass. nearest-exact is blocky "
                         "and 0.25 denoise only partly resolves it; bislerp is the "
                         "smoother community default - A/B them on one seed")
    ap.add_argument("--auto-story", action="store_true",
                    help="let the local model write the story itself")
    ap.add_argument("--premise", default=None,
                    help="one line premise for the auto-written story")
    ap.add_argument("--train-refs", default=None,
                    help="folder of your own reference images: train a style before drawing")
    ap.add_argument("--train-name", default=None,
                    help="style name for --train-refs (required with it)")
    ap.add_argument("--train-steps", type=int, default=1200)
    ap.add_argument("--page-mode", action=argparse.BooleanOptionalAction, default=True,                    help="one image = one finished page, lettered afterwards by compose.py "
                         "(default on; --no-page-mode goes back to rendering separate "
                         "panels and pasting them onto a sheet)")
    ap.add_argument("--page-panels", type=int, choices=[2, 4], default=None,
                    help="force the panel count on every page (2koma / 4koma)")

    # ---- automatic supervision -------------------------------------------------
    # Thinking is a per-request chat-template switch and costs roughly 4 minutes per
    # call on this CPU (measured; see README). It is ON by default for the two stages
    # where it earns its keep, and each one can be switched off individually.
    ap.add_argument("--think-plan", action=argparse.BooleanOptionalAction, default=True,
                    help="let the planner reason before writing the storyboard (default on)")
    ap.add_argument("--judge", action=argparse.BooleanOptionalAction, default=True,
                    help="run the model-driven panel review on the finished panels "
                         "(dry: verdicts only go to audit.jsonl; default on)")
    ap.add_argument("--judge-vision", action=argparse.BooleanOptionalAction, default=True,
                    help="attach the rendered PNG to the judge call (default on; the "
                         "server must have been started with --mmproj)")
    ap.add_argument("--judge-think", action=argparse.BooleanOptionalAction, default=True,
                    help="allow reasoning in the judge call (default on)")
    ap.add_argument("--judge-limit", type=int, default=6,
                    help="how many panels the judge reviews, worst intent coverage first. "
                         "The judge costs ~13 s per panel now that its image is downscaled "
                         "to 512, so a full pass over 40 panels is about 9 minutes")
    ap.add_argument("--judge-reroll", action=argparse.BooleanOptionalAction, default=True,
                    help="act on the judge's 'reroll' verdicts: queue a replacement final "
                         "render with a new seed, then render it before publishing "
                         "(default on). Bounded to one second chance per page and "
                         "--judge-reroll-max per run")
    ap.add_argument("--judge-reroll-max", type=int, default=4,
                    help="cap on rerolls the judge may queue in one run (default 4)")
    ap.add_argument("--judge-think-below", type=float, default=0.5,
                    help="judge thinks only about panels below this intent-coverage "
                         "ratio (default 0.5); pass 1.0 to always think")
    ap.add_argument("--clean-queue", action="store_true",
                    help="archive jobs.jsonl/state.json/qc.json/audit.jsonl before "
                         "starting. Needed when a previous run was paused: runner.py "
                         "renders every pending job, so leftovers are re-rendered at full "
                         "cost with the style they were planned with")
    args = ap.parse_args()

    LOGS.mkdir(parents=True, exist_ok=True)
    RUN_LOG = LOGS / ("run_%s.log" % time.strftime("%Y%m%d_%H%M%S"))
    t_all = time.time()

    if args.clean_queue:
        # Archive the contract set rather than deleting it: a paused run may still be
        # worth finishing, and state.json is the only record of what it had rendered.
        stamp = time.strftime("%Y%m%d_%H%M%S")
        bak = ROOT / "workspace" / "jobs" / ("_archive_clean_%s" % stamp)
        bak.mkdir(parents=True, exist_ok=True)
        moved = []
        for name in ("jobs.jsonl", "state.json", "qc.json", "audit.jsonl"):
            src = ROOT / "workspace" / "jobs" / name
            if src.exists():
                shutil.move(str(src), str(bak / name))
                moved.append(name)
        log("--clean-queue: archived %s -> %s" % (", ".join(moved) or "nothing", bak.name))

    log("orchestration start | story=%s panels=%d" % (args.story, args.panels))
    if args.think_plan or (args.judge and args.judge_think):
        log("thinking enabled: plan=%s, judge=%s (judge only below %.0f%% intent "
            "coverage) - a thinking call costs roughly +1.5 min on the GPU build "
            "(+4 min if the agent fell back to CPU). The server must have been started "
            "by the current launchers, otherwise --reasoning-budget does not cap it"
            % (args.think_plan, bool(args.judge and args.judge_think),
               args.judge_think_below * 100))
    pre_ids = {str(j.get("id")) for j in load_jobs()}  # so we publish only this run's output

    # runner.py renders EVERY pending job, not just this run's. pre_ids only keeps the
    # stale ones out of the published pages - they are still rendered, at full cost and
    # with whatever style was attached when they were planned. Measured: a paused 2B run
    # left 34 pending finals in the queue and a komi run would have rendered all of them,
    # ~90 minutes of wrong-style output that would then be silently discarded.
    if not args.clean_queue:
        _state = {}
        try:
            _state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
        stale = [j for j in load_jobs() if str(j.get("id")) not in _state]
        if stale:
            log("WARNING: %d pending job(s) from an earlier run are still queued and "
                "runner.py renders those too (%s ...). Re-run with --clean-queue to "
                "archive them first." % (len(stale), ", ".join(str(j.get("id"))
                                                              for j in stale[:5])))

    if args.train_refs and not args.train_name:
        log("--train-refs needs --train-name")
        return 2

    if not ensure_service("ComfyUI", COMFY_URL, "start_comfyui_headless.ps1"):
        return 3
    # The brain is deliberately NOT started here. On a fresh start ComfyUI may still be
    # holding the card from a previous run, and the agent would then be launched on the
    # CPU build for the whole run. stage(needs="llm") frees ComfyUI first, so the agent
    # gets the whole GPU instead of whatever happened to be left.
    if not args.train_refs and not (args.auto_story or args.premise):
        log("no LLM stage before the render - agent will start there if one needs it")

    # 1) train a style from your own reference set (prep -> train -> probe -> deploy)
    if args.train_refs:
        dst, staged = stage_reference_set(args.train_refs, args.train_name, ROOT / "workspace" / "refs")
        log("reference set: %d new image(s) staged into %s" % (staged, dst))
        if not stage("style training (%s, %d steps)" % (args.train_name, args.train_steps),
                     [PIPE / "style_train.py", "run", "--name", args.train_name,
                      "--steps", args.train_steps], needs="gpu"):
            return 3
        if not args.style:
            args.style = args.train_name

    # 2) story: use the file you wrote, or let the local model write one
    if args.auto_story or args.premise:
        story_args = [PIPE / "agent.py", "story", "--panels", args.panels, "--out", args.story]
        if args.premise:
            story_args += ["--premise", args.premise]
        if args.sheet:
            story_args += ["--sheet", args.sheet]
        if args.adult:
            story_args += ["--adult"]
        if not stage("agent story", story_args, needs="llm"):
            return 4
        if not args.title:
            args.title = read_story_title(args.story)

    if not Path(args.story).exists():
        log("story file not found: %s (pass --auto-story, --premise or write one)" % args.story)
        return 2

    plan_args = [PIPE / "agent.py", "plan", "--story", args.story,
                 "--panels", args.panels, "--draft"]
    if args.page_mode:
        # a page is portrait and the panels live inside the image
        plan_args += ["--page-mode", "--width", "832", "--height", "1216"]
        if args.page_panels:
            plan_args += ["--page-panels", str(args.page_panels)]
    else:
        # Both branches must be explicit. agent.py's --page-mode defaults to True, so
        # omitting the flag here silently planned PAGES even when orchestrate was asked
        # for separate panels: measured, a --no-page-mode run came back with 2koma/4koma
        # and `speech bubble` in all 40 prompts and 1024x1024 sizes, i.e. exactly the mode
        # it was told not to use. Selecting silence to mean "the other one" only works
        # when the default is on your side.
        plan_args += ["--no-page-mode", "--width", "832", "--height", "1216"]
    if args.style:
        plan_args += ["--style", args.style]
    if args.sheet:
        plan_args += ["--sheet", args.sheet]
    if args.ref_img:
        plan_args += ["--ref-img"] + [str(p) for p in args.ref_img]
        plan_args += ["--ref-strength", str(args.ref_strength)]
    if args.auto_preset:
        plan_args += ["--auto-preset", str(args.auto_preset)]
        if args.auto_preset_adult:
            plan_args += ["--auto-preset-adult"]
    if args.auto_artist:
        plan_args += ["--auto-artist", str(args.auto_artist)]
    if args.adult:
        plan_args += ["--adult"]
    if args.think_plan:
        plan_args += ["--think"]
    if not stage("agent plan (draft)", plan_args, needs="llm"):
        return 4
    if not stage("render drafts", [PIPE / "runner.py"], needs="gpu"):
        return 4
    stage("qc gate", [PIPE / "qc.py"], allow_fail=True)
    stage("agent review", [PIPE / "agent.py", "review", "--max-reroll", args.max_reroll],
          allow_fail=True, needs="llm")

    # anything the review queued needs rendering too
    if not stage("render rerolls", [PIPE / "runner.py"], needs="gpu"):
        return 4

    if args.skip_final:
        if args.page_mode:
            return publish_pages(pre_ids, t_all)
        log("skip-final set: stopping after drafts")
        return 0

    # finalize only THIS run's drafts: otherwise leftovers from earlier runs get
    # upgraded to full quality and published alongside the new work
    fresh_drafts = [str(j["id"]) for j in load_jobs()
                    if str(j.get("id")) not in pre_ids and j.get("turbo")]
    if fresh_drafts:
        finalize_args = [PIPE / "agent.py", "finalize", "--ids"] + fresh_drafts
        if args.hires:
            finalize_args += ["--hires", str(args.hires),
                              "--hires-denoise", str(args.hires_denoise),
                              "--hires-method", str(args.hires_method)]
        if not stage("agent finalize", finalize_args, needs="llm"):
            return 4
        if not stage("render finals", [PIPE / "runner.py"], needs="gpu"):
            return 4
    else:
        log("no drafts from this run to finalize")

    # Model-driven review of the panels that were just rendered at final quality.
    # Dry by design: verdicts land in audit.jsonl, nothing is re-queued here, so this
    # stage can never change the page - it only tells you what a model thinks of it.
    # Placed before the page-mode return on purpose: page mode copies these very same
    # panels out as finished pages, so both modes want them reviewed.
    if args.judge:
        judge_args = [PIPE / "agent.py", "judge",
                      "--limit", str(args.judge_limit),
                      "--think-below", str(args.judge_think_below)]
        if args.judge_vision:
            judge_args += ["--vision"]
        if args.judge_think:
            judge_args += ["--think"]
        if args.judge_reroll:
            # the judge may now queue replacements, so give it a budget to spend
            judge_args += ["--reroll", "--reroll-max", str(args.judge_reroll_max)]
        stage("agent judge (final panels)", judge_args, allow_fail=True, needs="llm")
        if args.judge_reroll:
            # Render whatever the judge queued. runner.py is a no-op when nothing is
            # pending, so this costs nothing on a run where every panel was kept - and
            # publish_pages picks the reroll over the final it replaced.
            if not stage("render judge rerolls", [PIPE / "runner.py"], allow_fail=True,
                         needs="gpu"):
                log("reroll render failed - publishing the pre-reroll pages")

    if args.page_mode:
        return publish_pages(pre_ids, t_all)

    # Grid mode: each PAGE is cols*rows separately rendered panels. This used to compose a
    # single page from the last cols*rows finals, which is fine for a one-page test and
    # useless for a book - a 40-page run needs 40 compose calls over the whole run's
    # finals, in story order.
    finals = [str(j["id"]) for j in load_jobs()
              if str(j.get("id", "")).endswith("f") and str(j.get("id")) not in pre_ids]
    if not finals:
        log("no final panels to compose")
        return 5
    per = max(1, args.cols * args.rows)
    groups = [finals[i:i + per] for i in range(0, len(finals), per)]
    log("grid mode: %d panel(s) -> %d page(s) of %dx%d"
        % (len(finals), len(groups), args.cols, args.rows))
    made = []
    for n, grp in enumerate(groups, 1):
        out = PAGES / ("page_%03d.png" % n)
        compose_args = [PIPE / "compose.py", "--cols", args.cols, "--rows", args.rows,
                        "--panels", ",".join(grp), "--out", out]
        if args.title and n == 1:
            compose_args += ["--title", args.title]
        # one bad page must not lose the other 39; its panels stay in out/panels
        if not stage("compose page %d/%d" % (n, len(groups)), compose_args, allow_fail=True):
            log("  page %d failed - panels kept in %s" % (n, PANELS))
            continue
        made.append(out.name)
    log("grid mode: %d page image(s) -> %s" % (len(made), PAGES))
    for m in made:
        log("  %s" % m)

    log("ALL DONE in %.1f min | panels=%s" % ((time.time() - t_all) / 60, ",".join(finals)))
    log("run log: %s" % RUN_LOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())