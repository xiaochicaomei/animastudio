"""Verify comfy_yield() actually waits for the VRAM, and the launcher then picks CUDA.

Before the fix comfy_yield slept a flat 2 s, the launcher sampled free VRAM once and found
only ~1.5 GiB, and the agent silently ran the whole run on the CPU build (9x slower).
"""
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
sys.path.insert(0, str(ROOT / "pipeline"))
import orchestrate as O  # noqa: E402


def agent_up():
    try:
        urllib.request.urlopen("http://127.0.0.1:8080/v1/models", timeout=3).read()
        return True
    except Exception:
        return False


print("=== 停掉当前 agent，确保从显存未释放的状态开始 ===")
subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(ROOT / "tools" / "stop_agent.ps1")], cwd=str(ROOT))
time.sleep(2)
print("   agent up =", agent_up())

print("=== comfy_yield()（修复后：轮询到真实显存）===")
t0 = time.time()
O.comfy_yield()
print("   用时 %.1fs" % (time.time() - t0))

print("=== 立刻启动 agent，看它选哪个构建 ===")
subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                  "-File", str(ROOT / "tools" / "start_agent_headless.ps1")],
                 cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
t0 = time.time()
while time.time() - t0 < 220 and not agent_up():
    time.sleep(2)
mode = O.agent_mode()
print("   agent up =", agent_up(), "in %.0fs" % (time.time() - t0))
print("   启动器报告:", mode)

if "ngl=99" in mode and "exe=llamacpp " in mode.replace("exe=llamacpp-cpu", "exe=CPU"):
    print()
    print("RESULT: PASS - agent 跑在 GPU 上（ngl=99）")
    sys.exit(0)
print()
print("RESULT: FAIL - agent 掉回 CPU")
sys.exit(1)
