"""4-beat A/B: page mode (one composed page) vs per-panel (4 renders + code grid).

Same beats, same style, same hires setting on both sides - the only variable is HOW the
page is produced. Measures the thing the whole argument rests on: pixels per panel.

    python _stage\\ab_4panels.py
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
PY = ROOT / "ComfyUI_windows_portable" / "python_embeded" / "python.exe"
JOBS = ROOT / "workspace" / "jobs"
PIPELINE = ROOT / "pipeline"
sys.path.insert(0, str(PIPELINE))
import orchestrate as O  # noqa: E402

STORY = JOBS / "ab4.txt"
STORY.write_text(
    "# 雨夜\n"
    "秀子站在厨房水槽前洗碗，窗外的雨敲打着玻璃\n"
    "年轻邻居敲响后门，浑身湿透，手里拎着一把坏掉的伞\n"
    "秀子递给他一条干毛巾，两人的手指在毛巾上碰了一下\n"
    "雨声里，秀子低下头，没有把手收回去\n",
    encoding="utf-8")

SHEET = JOBS / "xiaozi_sheet.txt"
STYLE = "sodalord_style"


def agent_up():
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:8080/v1/models", timeout=3).read()
        return True
    except Exception:
        return False


def run(cmd, label):
    t0 = time.time()
    r = subprocess.run([str(PY), "-u"] + [str(c) for c in cmd], cwd=str(PIPELINE),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    print("--- %s  rc=%d  %.0fs" % (label, r.returncode, time.time() - t0))
    for line in out.splitlines():
        if any(k in line for k in ("planned", "DONE", "pass finished", "style attached",
                                   "page written", "FAILED", "Error", "error")):
            print("     " + line.strip()[:150])
    return r.returncode


print("=== 0. 把 GPU 交给 agent 来规划 ===")
O.comfy_yield()
if not agent_up():
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                      "-File", str(ROOT / "tools" / "start_agent_headless.ps1")],
                     cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    t0 = time.time()
    while time.time() - t0 < 200 and not agent_up():
        time.sleep(2)
print("   agent up =", agent_up())

COMMON = ["--story", STORY, "--panels", "4", "--style", STYLE, "--sheet", SHEET,
          "--hires", "1.5", "--hires-denoise", "0.25", "--hires-method", "nearest-exact"]

print()
print("=== 1. A 组：page mode（模型排版的整页）===")
run([PIPELINE / "agent.py", "plan"] + COMMON + ["--page-mode", "--width", "832",
                                                "--height", "1216"], "plan A (page mode)")

print()
print("=== 2. B 组：逐格（4 张独立面板）===")
run([PIPELINE / "agent.py", "plan"] + COMMON + ["--no-page-mode", "--width", "832",
                                                "--height", "1216"], "plan B (per-panel)")

print()
print("=== 3. 渲染全部（GPU 交给渲染器）===")
O.agent_release()
run([PIPELINE / "runner.py"], "render all")

print()
print("=== 4. 结果 ===")
import io
import json
from PIL import Image
jobs = [json.loads(l) for l in (JOBS / "jobs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
for j in jobs:
    f = ROOT / "workspace" / "out" / "panels" / ("%s.png" % j["id"])
    tag = "A 整页" if j.get("turbo") is False and "comic" in (j.get("prompt") or "") else "B 单格"
    if f.exists():
        im = Image.open(f)
        print("   %-8s %-8s %sx%s  %.2f MP  hires=%s" % (
            j["id"], tag, im.width, im.height, im.width * im.height / 1e6, j.get("hires")))
    else:
        print("   %-8s 未出图" % j["id"])
