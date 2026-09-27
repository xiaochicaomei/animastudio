"""Test T1-D: does plan resume from a partially-planned story instead of starting over?

Simulates the failure that actually happened - a 40-page plan that died at chunk 9 and
lost every chunk before it. Runs IN PROCESS with agent.JOBS pointed at a temp file, so it
cannot touch the real queue.

    python _stage\\test_plan_resume.py
"""
import contextlib
import io as _io
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
sys.path.insert(0, str(ROOT / "pipeline"))
import agent  # noqa: E402
import orchestrate as O  # noqa: E402


def agent_up():
    try:
        urllib.request.urlopen("http://127.0.0.1:8080/v1/models", timeout=3).read()
        return True
    except Exception:
        return False


print("=== 0. agent ===")
O.comfy_yield()
if not agent_up():
    import subprocess
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                      "-File", str(ROOT / "tools" / "start_agent_headless.ps1")],
                     cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    t0 = time.time()
    while time.time() - t0 < 200 and not agent_up():
        time.sleep(2)
print("   up =", agent_up())
print()

tmp = Path(tempfile.mkdtemp())
agent.JOBS = tmp / "jobs.jsonl"          # never touch the real queue
story = tmp / "story.txt"
story.write_text("# 测试\n" + "\n".join("第%d个画面：她在房间里做第%d件事" % (i, i)
                                        for i in range(1, 9)) + "\n", encoding="utf-8")
partial = story.with_suffix(".plan.partial.jsonl")

planted = [{"id": i, "panel_count": 2, "shot": "medium shot", "scene": "房间",
            "pose_action": ["standing", "looking at viewer"],
            "tags": ["indoors", "mature female"],
            "dialogue": "第%d块预置台词" % i} for i in (1, 2, 3, 4)]
partial.write_text(json.dumps({"start": 0, "total": 8, "n": 4, "page_mode": True,
                               "sheet": {"appearance": ["mature female", "purple eyes"],
                                         "props": []},
                               "panels": planted}, ensure_ascii=False) + "\n",
                   encoding="utf-8")
print("=== 1. 植入一个已完成的 chunk 0（4 页），然后规划 8 页 ===")
sys.argv = ["agent.py", "plan", "--story", str(story), "--panels", "8", "--draft",
            "--page-mode", "--width", "832", "--height", "1216"]
buf = _io.StringIO()
t0 = time.time()
with contextlib.redirect_stdout(buf):
    rc = agent.main()
out = buf.getvalue()
print("   rc=%d  %.0fs" % (rc, time.time() - t0))
for line in out.splitlines():
    if any(k in line for k in ("resuming", "chunk", "planned", "character sheet", "style")):
        print("     " + line.strip())
print()

jobs = [json.loads(l) for l in agent.JOBS.read_text(encoding="utf-8").splitlines() if l.strip()]
dlg = [j.get("dialogue") for j in jobs]
print("=== 2. 结果 ===")
print("   落盘页数        : %d (期望 8)" % len(jobs))
print("   预置台词是否保留: %s" % ("是 -> %s" % dlg[:4] if any(d and "预置" in d for d in dlg)
                                   else "否（chunk 0 被重做了）"))
print("   sidecar 已清理  : %s (期望 True)" % (not partial.exists()))
ok = (len(jobs) == 8 and any(d and "预置" in d for d in dlg) and not partial.exists())
print()
print("RESULT:", "PASS - 断点续跑生效" if ok else "FAIL")
sys.exit(0 if ok else 1)
