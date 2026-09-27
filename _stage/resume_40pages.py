"""Resume the 40-page run from the finals stage.

The first attempt died in `render finals` on an UnboundLocalError in
runner.style_registry() - a missing `global`, since fixed. Everything before that point
survived intact: 40 drafts are recorded in state.json and 40 finals (with hires=1.5) are
already queued in jobs.jsonl. Re-planning would throw that away, so this replays only the
tail of orchestrate's sequence - through orchestrate's own stage() and publish_pages(),
so the GPU handoff and the page publishing behave exactly as in a normal run.

    python _stage\\resume_40pages.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
sys.path.insert(0, str(ROOT / "pipeline"))
import orchestrate as O  # noqa: E402

O.RUN_LOG = O.LOGS / ("run_%s_resume.log" % time.strftime("%Y%m%d_%H%M%S"))
t_all = time.time()

# jobs.jsonl was archived at the start of this run, so every job in it belongs to it
PRE_IDS = set()

O.log("resume start | drafts done, finals queued | log=%s" % O.RUN_LOG.name)

if not O.stage("render finals", [O.PIPE / "runner.py"], needs="gpu"):
    O.log("finals FAILED - stopping before judge/compose")
    sys.exit(4)

# mirrors orchestrate's defaults: --judge-limit 6 --judge-think-below 0.5, vision on,
# thinking on, reroll on with a budget of 4
judge_args = [O.PIPE / "agent.py", "judge",
              "--limit", "6", "--think-below", "0.5",
              "--vision", "--think", "--reroll", "--reroll-max", "4"]
O.stage("agent judge (final panels)", judge_args, allow_fail=True, needs="llm")
O.stage("render judge rerolls", [O.PIPE / "runner.py"], allow_fail=True, needs="gpu")

sys.exit(O.publish_pages(PRE_IDS, t_all))
