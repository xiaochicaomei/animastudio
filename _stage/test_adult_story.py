"""A/B the story stage: does --adult actually turn mood into physical beats?

The 40-page run failed because the story stage never asked for adult content, so the 9B
wrote 40 sentences of atmosphere from an NTR premise and cut away at the one moment of
intimacy. This runs the same premise both ways and scores the difference on the thing
that actually went wrong: how many beats describe a physical act, and whether the beats
that lead somewhere are followed through instead of cut away from.

    python _stage\\test_adult_story.py
"""
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
PY = ROOT / "ComfyUI_windows_portable" / "python_embeded" / "python.exe"
sys.path.insert(0, str(ROOT / "pipeline"))
import orchestrate as O  # noqa: E402

PREMISE = (ROOT / "workspace" / "jobs" / "xiaozi_premise.txt").read_text(encoding="utf-8").strip()

# words that mark a beat as describing a body/act rather than a feeling
PHYSICAL = ("胸", "乳", "臀", "腿", "腰", "唇", "舌", "手", "指", "皮肤", "身体", "裸",
            "脱", "衣", "内裤", "胸罩", "吻", "舔", "抚", "摸", "揉", "插", "进", "入",
            "交合", "性", "欲", "勃", "喘", "呻吟", "湿", "高潮", "射")
MOOD = ("感到", "觉得", "心中", "内心", "情感", "思绪", "心情", "仿佛", "似乎")


def agent_up():
    try:
        urllib.request.urlopen("http://127.0.0.1:8080/v1/models", timeout=3).read()
        return True
    except Exception:
        return False


print("=== 0. 把 GPU 交给 agent ===")
O.comfy_yield()
if not agent_up():
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                      "-File", str(ROOT / "tools" / "start_agent_headless.ps1")],
                     cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    t0 = time.time()
    while time.time() - t0 < 200 and not agent_up():
        time.sleep(2)
print("   agent up =", agent_up())
print("   " + O.agent_mode())
print()

results = {}
for tag, extra in (("tame (无 --adult)", []), ("adult (--adult)", ["--adult"])):
    out = ROOT / "workspace" / "jobs" / ("_test_story_%s.txt" % ("adult" if extra else "tame"))
    cmd = [str(PY), "-u", str(ROOT / "pipeline" / "agent.py"), "story",
           "--premise", PREMISE, "--panels", "40", "--out", str(out)] + extra
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = [l.strip() for l in out.read_text(encoding="utf-8").splitlines() if l.strip()] if out.exists() else []
    beats = lines[1:]
    phys = [b for b in beats if any(w in b for w in PHYSICAL)]
    mood = [b for b in beats if any(w in b for w in MOOD)]
    results[tag] = (beats, phys, mood)
    print("=== %s  rc=%d  %.0fs   共 %d beat ===" % (tag, r.returncode, time.time() - t0, len(beats)))
    print("   含身体/动作词 : %d  (%.0f%%)" % (len(phys), 100.0 * len(phys) / max(1, len(beats))))
    print("   含情绪/感受词 : %d  (%.0f%%)" % (len(mood), 100.0 * len(mood) / max(1, len(beats))))
    print("   正文尾部:")
    for b in beats[-8:]:
        print("      " + b)
    print()

tame_phys = len(results["tame (无 --adult)"][1])
adult_phys = len(results["adult (--adult)"][1])
print("=== 结论 ===")
print("   身体/动作 beat:  tame=%d  ->  adult=%d" % (tame_phys, adult_phys))
if adult_phys > tame_phys:
    print("   RESULT: PASS - --adult 确实把情绪换成了动作")
    sys.exit(0)
print("   RESULT: FAIL - --adult 没有改变 beat 的性质")
sys.exit(1)
