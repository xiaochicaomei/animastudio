"""Prove the GPU handoff cycle orchestrate now performs at every stage boundary.

The design leans on things that are easy to assume and expensive to get wrong:
  1. ComfyUI reloads its models by itself after /free, so yielding the GPU costs a
     reload rather than a restart.
  2. The launcher can only be observed via Popen + polling - a capturing caller blocks
     on the handle the spawned server inherits.
  3. The 40-block 2.9B Anima still renders correctly after the agent has taken and
     released the whole card in between.
"""
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
sys.path.insert(0, str(ROOT / "pipeline"))

import orchestrate  # noqa: E402
import runner  # noqa: E402
from comfy import Comfy  # noqa: E402

c = Comfy()
fails = []


def say(msg):
    print(msg, flush=True)


def vram_free_mib():
    try:
        return c.vram_free()[0] / 2 ** 20
    except Exception:
        return -1.0


def agent_up():
    try:
        urllib.request.urlopen("http://127.0.0.1:8080/v1/models", timeout=3).read()
        return True
    except Exception:
        return False


def start_agent():
    """The pattern ensure_service uses: spawn, then poll. Never capture its output."""
    t0 = time.time()
    p = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(ROOT / "tools" / "start_agent_headless.ps1")],
        cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    while time.time() - t0 < 200:
        if agent_up():
            return True, time.time() - t0
        if p.poll() is not None and not agent_up():
            pass
        time.sleep(2)
    return False, time.time() - t0


def render(tag, job):
    job = dict(job)
    job["prefix"] = tag
    wf = runner.build_workflow(job)
    t0 = time.time()
    qid = c.submit(wf)
    entry, _ = c.wait(qid)
    st = c.status_str(entry)
    dt = time.time() - t0
    say("   render %-6s unet=%-34s status=%-8s images=%d  %.1fs"
        % (tag, wf["1"]["inputs"]["unet_name"], st, len(c.output_images(entry)), dt))
    if st != "success":
        fails.append("%s render failed: %s" % (tag, c.error_messages(entry)))
    return st == "success"


say("=== 1. warm ComfyUI with a draft (its models land in VRAM) ===")
render("ho_a", {"id": "ho_a", "width": 512, "height": 512, "seed": 11, "turbo": True,
                "steps": 8, "prompt": "1girl, solo, gentle smile, upper body"})
warm = vram_free_mib()
say("   free VRAM while models are held : %.0f MiB" % warm)

say("=== 2. orchestrate.comfy_yield() ===")
orchestrate.comfy_yield()
time.sleep(2)
after_yield = vram_free_mib()
say("   free VRAM after yield           : %.0f MiB" % after_yield)
if after_yield <= warm + 500:
    fails.append("comfy_yield did not free VRAM (%.0f -> %.0f)" % (warm, after_yield))

say("=== 3. start the agent on the freed card ===")
ok, dt = start_agent()
say("   agent ready=%s in %.0fs" % (ok, dt))
say("   launcher said: %s" % orchestrate.agent_mode())
if not ok:
    fails.append("agent did not become ready")
elif "ngl=99" not in orchestrate.agent_mode():
    fails.append("agent did not fully offload: %s" % orchestrate.agent_mode())

say("=== 4. orchestrate.agent_release() ===")
t0 = time.time()
orchestrate.agent_release()
time.sleep(2)
say("   released in %.1fs, agent_up=%s, free VRAM=%.0f MiB"
    % (time.time() - t0, agent_up(), vram_free_mib()))
if agent_up():
    fails.append("agent still serving after agent_release()")
if vram_free_mib() < 6000:
    fails.append("agent_release did not give the card back (%.0f MiB free)" % vram_free_mib())

say("=== 5. ComfyUI must reload by itself, and 2.9B must still render ===")
render("ho_b", {"id": "ho_b", "width": 1024, "height": 1024, "seed": 22, "turbo": False,
                "steps": 40, "prompt": "1girl, solo, upper body, gentle smile"})

say("=== 6. second cycle: yield, resume the agent, release ===")
orchestrate.comfy_yield()
ok2, dt2 = start_agent()
say("   second cycle ready=%s in %.0fs -> %s" % (ok2, dt2, orchestrate.agent_mode()))
if not ok2:
    fails.append("agent did not come back up on the second cycle")
orchestrate.agent_release()

say("")
if fails:
    say("RESULT: FAIL")
    for f in fails:
        say("  - " + f)
    sys.exit(1)
say("RESULT: PASS - yield -> agent -> release -> reload all work")
