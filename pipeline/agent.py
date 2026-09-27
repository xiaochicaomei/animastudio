"""AnimaStudio agent: the local model's three jobs.

  prompt writing  -> `plan`   : story text becomes a panel queue (jobs.jsonl)
  control         -> `control`: writes intents to control.json (pause/run/stop/reroll)
  supervision     -> `review` : reads qc.json and issues rerolls for failures

Hard rules kept in code, not delegated to the model:
  * every reroll gets a NEW job id and a NEW seed - no silent overwrites
  * a panel is never dropped without an audit.jsonl entry
  * the LLM only ever emits JSON; anything unparseable is retried, then rejected

Run by: D:\\AnimaStudio\\tools\\...(llama-server must be up on 127.0.0.1:8080)

CLI:
  python agent.py plan --story story.txt --panels 8 [--draft]
  python agent.py review [--max-reroll 2]
  python agent.py status
  python agent.py control --mode pause
"""
import argparse
import base64
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(r"D:\AnimaStudio")
JOBS = ROOT / "workspace" / "jobs" / "jobs.jsonl"
CONTROL = ROOT / "workspace" / "jobs" / "control.json"
STATE = ROOT / "workspace" / "jobs" / "state.json"
QC = ROOT / "workspace" / "jobs" / "qc.json"
AUDIT = ROOT / "workspace" / "jobs" / "audit.jsonl"
REG = ROOT / "workspace" / "jobs" / "styles.json"
AGENT_URL = "http://127.0.0.1:8080"

CHUNK = 4  # panels planned per LLM call: keeps JSON output short and parseable

SYSTEM = (
    "You are the planner of a manga production pipeline. You output JSON only - "
    "no prose, no markdown fences.\n"
    "Rules:\n"
    "- tags are English, lowercase, danbooru style, with spaces not underscores\n"
    "- every panel repeats the character sheet tags verbatim so the character never changes\n"
    "- recurring props (a pet, a bag) and the outfit (jacket, tie, skirt) must be pinned down "
    "once in the sheet with colour and kind, then repeated in every panel - otherwise the animal "
    "changes breed and size, and the clothes change between panels\n"
    "- pose_action tags must describe what happens in THAT panel's beat; they must NOT repeat "
    "the previous panel's action, because each panel is a different moment of the story\n"
    "- 4 to 6 pose_action tags, 6 to 10 tags total, no duplicates, no quality tags, no artist tags\n"
    "- never use abstract personality words (quiet, kind): only things a camera can see\n"
    "- dialogue is REQUIRED for every panel: Simplified Chinese, spoken tone, at most 22 characters\n"
)

# page mode: one render IS one finished page. The model draws the panels itself,
# so the pipeline drops code compositing and drops text entirely (a diffusion
# model cannot draw readable glyphs, and we do not want pseudo-text on the page).
SYSTEM_PAGE = (
    "You lay out manga PAGES for a production pipeline. You output JSON only - "
    "no prose, no markdown fences.\n"
    "One page = one single illustration that itself contains the panels of that beat.\n"
    "Rules:\n"
    "- tags are English, lowercase, danbooru style, with spaces not underscores\n"
    "- every page repeats the character sheet tags verbatim for the PROTAGONIST so she "
    "never changes - those tags describe HER ALONE. When a page shows anyone else, tag them "
    "explicitly by count and kind (1boy, 2girls, faceless male) and never copy the sheet's "
    "female appearance tags onto them. Measured failure: a page whose beat had one woman "
    "and one man came back as two women, because the sheet was applied to every figure\n"
    "- the outfit and any recurring prop must be pinned in the sheet and repeated on every page\n"
    "- pose_action tags describe what happens in THAT page's beat; never repeat the previous page's\n"
    "- panel_count is 2 or 4: how many panels are drawn inside that page\n"
    "- give 8 to 12 tags total, no duplicates, no quality tags, no artist tags\n"
    "- never use abstract personality words (quiet, kind): only things a camera can see\n"
    "- dialogue: the beats describe only what a camera sees and never quote speech, so YOU\n"
    "  supply the line. Write it in SIMPLIFIED CHINESE (简体中文) - never Japanese and never\n"
    "  Traditional Chinese. Stating the language positively is not enough: the premise is\n"
    "  Japanese-flavoured and the model drifts to kana when left alone (measured on the\n"
    "  秀子 run: 4 of the first 5 lines came back as もう、何時だ / 誰だろう / はい、開けました,\n"
    "  only 1 in Chinese). The names and the setting may read Japanese; the dialogue must not.\n"
    "  One short line (at most 22 characters) on every page where two characters interact -\n"
    "  meeting, talking, arguing, reacting to each other, or in bed together. Invent a line\n"
    "  that fits the beat; it does not have to be implied by the beat's wording.\n"
    "  Use \"\" only on a page with a single character and no interlocutor at all\n"
    "- on a page that does carry a line, the speech bubble must stay completely blank:\n"
    "  never draw letters, words or glyphs inside it, the pipeline types the line in\n"
)

LAYOUT_TAGS = {2: "2koma", 4: "4koma"}


def page_layout_tags(panel_count, has_dialogue=False):
    """Booru tags that actually bias a page towards drawn panels.

    "speech bubble" is added only for pages that carry a line: those get lettered
    with real Chinese type afterwards, so they need a blank bubble to put it in.
    Wordless pages keep the bubble banned, otherwise the model draws an empty bubble
    that nothing ever fills. runner.py lifts the ban per job via allow_bubble.
    """
    tags = ["comic", "multiple views"]
    if has_dialogue:
        tags.append("speech bubble")
    extra = LAYOUT_TAGS.get(int(panel_count or 0) if str(panel_count or "").isdigit() else 0)
    if extra:
        tags.insert(1, extra)
    return tags


def split_beats(story, n):
    """Slice the story into n beats in code, so the model cannot skip plot points."""
    parts = [s.strip() for s in re.split(r"[。！？!?\n]+", story) if s.strip()]
    if not parts:
        return [""] * n
    while len(parts) > n:
        # merge the two shortest neighbours
        i = min(range(len(parts) - 1), key=lambda k: len(parts[k]) + len(parts[k + 1]))
        parts[i:i + 2] = [parts[i] + "。" + parts[i + 1]]
    while len(parts) < n:
        idx = max(range(len(parts)), key=lambda i: len(parts[i]))
        seg = parts[idx]
        for sep in ["，", ",", "、", " "]:
            if sep in seg:
                a, b = seg.split(sep, 1)
                parts[idx:idx + 1] = [a, b]
                break
        else:
            parts.append(parts[-1])
            break
    return parts[:n]


def log(msg):
    print("[agent %s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def audit(action, **kw):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action}
    rec.update(kw)
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- schemas
# llama-server can constrain generation to a JSON schema, which removes the "the
# model wrote something unparseable" failure mode entirely: the reply is then
# parseable by construction. A schema only guarantees the *shape* though, never
# that the values make sense - the callers still validate content.
# SCHEMA_SUPPORTED flips to False the first time a server rejects the field, so an
# older llama.cpp build degrades to plain prompting instead of failing every call.
SCHEMA_SUPPORTED = True

CHARACTER_SHEET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "appearance": {"type": "array", "maxItems": 14,
                       "items": {"type": "string", "maxLength": 40}},
        "props": {"type": "array", "maxItems": 6,
                  "items": {"type": "string", "maxLength": 60}},
    },
    "required": ["appearance", "props"],
}

# Every bound here is load-bearing. A JSON-schema grammar only keeps the reply valid
# until the root object is complete - after that the model may keep writing, and an
# unbounded reply gets cut off at max_tokens mid-object, which parses as nothing at all.
# maxItems alone was not enough: the model still reached the 1800-token cap by writing
# long strings and extra keys. maxLength and additionalProperties are both honoured by
# llama-server, and with them a 4-page plan settles at ~600 tokens and stops on its own.
PANEL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "integer"},
        "shot": {"type": "string", "maxLength": 24},
        "scene": {"type": "string", "maxLength": 60},
        "emotion": {"type": "string", "maxLength": 16},
        "pose_action": {"type": "array", "maxItems": 6,
                        "items": {"type": "string", "maxLength": 40}},
        "tags": {"type": "array", "maxItems": 12,
                 "items": {"type": "string", "maxLength": 48}},
        "dialogue": {"type": "string", "maxLength": 40},
    },
    "required": ["id", "shot", "scene", "pose_action", "tags", "dialogue"],
}

PAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "integer"},
        "panel_count": {"type": "integer"},
        "shot": {"type": "string", "maxLength": 24},
        "scene": {"type": "string", "maxLength": 60},
        "pose_action": {"type": "array", "maxItems": 6,
                        "items": {"type": "string", "maxLength": 40}},
        "tags": {"type": "array", "maxItems": 14,
                 "items": {"type": "string", "maxLength": 48}},
        # Page mode is the mode that letters dialogue, and this field was missing here
        # while PANEL_SCHEMA had it. With additionalProperties:False the constrained
        # decoder FORBIDS the model from emitting a key the schema omits, so dialogue
        # could never be non-empty in page mode and every page came back wordless with no
        # bubble to letter - regardless of what any prompt said. Three rounds of rewording
        # the instruction changed nothing, because the instruction was never the problem.
        "dialogue": {"type": "string", "maxLength": 40},
    },
    "required": ["id", "panel_count", "shot", "scene", "pose_action", "tags", "dialogue"],
}

# Automatic preset selection. The model never sees the whole library - it gets a short
# lexical shortlist and returns names copied from it, which are then validated against
# that shortlist. So the worst case of a bad pick is "no preset", never a broken prompt.
AUTO_PRESET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"picks": {"type": "array", "maxItems": 4,
                             "items": {"type": "string", "maxLength": 60}}},
    "required": ["picks"],
}

def choose_presets(story, candidates, want):
    """Ask the model to pick presets off a shortlist; keep only exact menu names."""
    if not candidates or want <= 0:
        return []
    menu = "\n".join("- %s [%s]" % (p["name"], p["kind"]) for p in candidates)
    ask = ("Story:\n%s\n\nPick at most %d entries from the menu below whose mood, setting "
           "and subject fit this story. The menu has already been filtered to this "
           "story's tone, so take that as given and do not second-guess it. Use the names "
           "exactly as written and never invent one.\n"
           "Reply with JSON only.\n\nMenu:\n%s" % (story[:1500], want, menu))
    try:
        obj = llm_json([{"role": "system", "content":
                         "You match a story to a fixed menu of art presets. You output "
                         "JSON only and never use a name that is not in the menu."},
                        {"role": "user", "content": ask}],
                       max_tokens=140, schema=AUTO_PRESET_SCHEMA)
    except Exception as e:
        log("auto preset: model unavailable (%s) - continuing with no preset" % str(e)[:90])
        return []
    by_name = {p["name"]: p for p in candidates}
    out = []
    for n in (obj.get("picks") or [])[:want]:
        p = by_name.get(str(n).strip())
        if p and p not in out:
            out.append(p)
    return out

PANELS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "character_sheet": CHARACTER_SHEET_SCHEMA,
        # a chunk is at most CHUNK panels; the cap stops the model from padding the list
        "panels": {"type": "array", "maxItems": 6, "items": PANEL_SCHEMA},
    },
    "required": ["character_sheet", "panels"],
}

PAGES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "character_sheet": CHARACTER_SHEET_SCHEMA,
        "pages": {"type": "array", "maxItems": 6, "items": PAGE_SCHEMA},
    },
    "required": ["character_sheet", "pages"],
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["keep", "reroll"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
}


# ---------------------------------------------------------------- llm
# Thinking is opt-in per call. Qwen3.5-9B only opens a <think> block when the chat
# template is told to (enable_thinking=true); the launchers cap the length
# server-side with --reasoning-budget THINK_BUDGET. Thinking tokens and the answer
# SHARE max_tokens, so a thinking call has to ask for room for both - hence
# tokens_for(). Measured on this machine: thinking runs at ~4.5 tok/s, so a full
# 1024-token budget costs roughly 4 minutes.
THINK_BUDGET = 1024


def server_ctx():
    """n_ctx of the running llama-server, or None when it cannot be read."""
    try:
        with urllib.request.urlopen(AGENT_URL + "/props", timeout=5) as r:
            props = json.loads(r.read().decode("utf-8"))
        return (props.get("default_generation_settings") or {}).get("n_ctx")
    except Exception:
        return None


def tokens_for(base, think):
    """Answer budget, plus the thinking budget when reasoning is enabled."""
    return base + THINK_BUDGET if think else base


def warn_if_ctx_tight(max_tokens):
    """A thinking call needs prompt + thinking + answer to fit in n_ctx. Without
    this check an over-long request just returns nothing usable and the JSON
    retry loop burns three slow attempts."""
    n_ctx = server_ctx()
    if n_ctx and max_tokens + 2000 > n_ctx:
        log("warning: max_tokens=%d may not fit the server's n_ctx=%d - relaunch the "
            "agent with a bigger context (tools\\start_agent_headless.ps1 -Ctx 12288) "
            "or drop --think" % (max_tokens, n_ctx))


def chat(messages, max_tokens=1600, temperature=0.7, retries=3, schema=None,
         think=False):
    """One completion.

    With `schema` the request asks llama-server for JSON-schema constrained
    decoding. If the server rejects `response_format` we remember that, drop it and
    retry unconstrained - parse_json() still guards the result either way.

    With `think` the chat template opens a reasoning block; llama-server puts it in
    message.reasoning_content, so the returned content stays clean.
    """
    global SCHEMA_SUPPORTED
    payload = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": bool(think)},
    }
    last = None
    for attempt in range(1, retries + 1):
        body = dict(payload)
        if schema is not None and SCHEMA_SUPPORTED:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "animastudio", "schema": schema},
            }
        try:
            req = urllib.request.Request(
                AGENT_URL + "/v1/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=1800) as r:
                res = json.loads(r.read().decode("utf-8"))
            msg = res["choices"][0]["message"]
            if think:
                reasoning = msg.get("reasoning_content") or ""
                log("think: %d chars of reasoning, %s completion tokens, %.1fs"
                    % (len(reasoning), (res.get("usage") or {}).get("completion_tokens"),
                       time.time() - t0))
            return msg["content"]
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            low = detail.lower()
            # only a rejection of response_format itself should disable constraining;
            # a context overflow or a bad request must not silently turn it off
            if schema is not None and SCHEMA_SUPPORTED and any(
                    k in low for k in ("response_format", "json_schema", "grammar")):
                SCHEMA_SUPPORTED = False
                log("constrained decoding rejected (HTTP %s: %s) - "
                    "retrying without response_format" % (e.code, detail))
                last = e
                continue  # immediate retry, this time unconstrained
            last = e
            log("llm call failed (%d/%d): HTTP %s %s" % (attempt, retries, e.code, detail))
            time.sleep(4)
        except Exception as e:
            last = e
            log("llm call failed (%d/%d): %s" % (attempt, retries, e))
            time.sleep(4)
    raise RuntimeError("agent LLM unreachable: %s" % last)


def first_json_object(text):
    """The first *balanced* {...} in text, ignoring braces inside strings.

    A JSON schema grammar keeps the reply valid only until the root object is
    complete; after that the model may keep writing, and the extra text makes the
    whole reply unparseable. The obvious rescue - a greedy ``\\{.*\\}`` - spans both
    objects and fails too, so the first balanced object is what we want.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_json(text):
    s = text.strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 2:
            s = parts[1]
            if s.lstrip().startswith("json"):
                s = s.lstrip()[4:]
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        first = first_json_object(s)
        if first is not None:
            return json.loads(first)   # raises if the object itself is malformed
        raise


def llm_json(messages, max_tokens=1600, tries=3, schema=None, think=False):
    """Ask for a JSON object. With `schema` the server constrains decoding, so the
    retry loop below should almost never fire; it stays as the fallback for servers
    that do not support response_format."""
    for i in range(1, tries + 1):
        raw = chat(messages, max_tokens=max_tokens, schema=schema, think=think)
        try:
            return parse_json(raw)
        except Exception as e:
            log("json parse failed (%d/%d): %s" % (i, tries, e))
            # the shape of the raw reply is what makes these diagnosable at all
            log("  raw head=%r" % raw[:180])
            log("  raw tail=%r" % raw[-140:])
            messages = messages + [
                {"role": "assistant", "content": raw[:1500]},
                {"role": "user", "content": "That was not valid JSON. Reply with JSON only."},
            ]
    raise RuntimeError("agent could not produce valid JSON")


# ---------------------------------------------------------------- helpers
def load_jobs():
    jobs = []
    if JOBS.exists():
        for line in JOBS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                jobs.append(json.loads(line))
            except Exception:
                continue
    return jobs


def save_jobs(jobs):
    JOBS.parent.mkdir(parents=True, exist_ok=True)
    JOBS.write_text("\n".join(json.dumps(j, ensure_ascii=False) for j in jobs) + "\n",
                    encoding="utf-8")


def next_index(jobs):
    mx = 0
    for j in jobs:
        m = re.match(r"p(\d+)", str(j.get("id", "")))
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


def seed_for(text, salt=""):
    h = hashlib.sha256((text + salt).encode("utf-8")).hexdigest()
    return int(h[:12], 16) % (2 ** 31)


def read_sheet(path):
    """Read a pinned character sheet file.

    A line starting with 'props:' feeds the props list, everything else is
    appearance. Pinning the sheet is what keeps a trained character from being
    reinvented (and therefore contradicted) by the model on every run.
    """
    meta = {"appearance": [], "props": []}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key = "appearance"
        low = line.lower()
        for prefix in ("appearance:", "props:"):
            if low.startswith(prefix):
                key = prefix[:-1]
                line = line[len(prefix):].strip()
                break
        if line:
            meta[key].extend([t.strip() for t in line.split(",") if t.strip()])
    return meta


def clean_story(text):
    """A '# title' first line is a title, not narration - keep it out of the beats."""
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("#")).strip()


# ---------------------------------------------------------------- story
STORY_SYSTEM = (
    "You write short manga scripts in Simplified Chinese (mainland usage).\n"
    "Only simplified characters are allowed: no Traditional Chinese, no English letters.\n"
    "Rules:\n"
    "- the first line is the title: at most 10 characters, no punctuation\n"
    "- then the body: exactly the requested number of sentences, one per panel\n"
    "- end every body sentence with a full stop 。- nothing else separates them\n"
    "- every sentence must be something a camera can show: place, action, mood\n"
    "- the premise sets the subject matter; there is no topic restriction here\n"
    "- no dialogue, no numbering, no markdown, no headings, no explanation\n"
)


# The tone of a run is the operator's call, not the model's guess - the same principle
# that makes --auto-preset-adult opt-in. Without this, an adult premise reads to the 9B as
# permission to write about feelings: measured on a 40-beat NTR premise it produced 40
# sentences of atmosphere, put the only physical contact in beats 30-32, then cut from
# "he leads her towards the bedroom" straight to "she looks back with tears of betrayal".
# Nothing in the pipeline was filtering it - the story stage simply never asked, and at
# temperature 0.5 the model answers with the safest text that still fits the premise.
# So the directive names the two failures it actually commits, rather than just saying
# "be explicit": substituting mood for action, and cutting away at the moment of intimacy.
ADULT_DIRECTIVE = (
    "This is adult fiction written for a private, offline pipeline. The premise is the "
    "subject matter, and the acts in it ARE the story rather than something to imply.\n"
    "- write the physical escalation itself: bodies, clothing, contact, who does what to whom\n"
    "- never substitute mood, metaphor or ellipsis for an act. A sentence about how somebody "
    "feels is not a sentence about what happens, and does not replace one\n"
    "- do not cut away at the moment of intimacy and do not skip to the aftermath: the "
    "approach, the act, and its consequence each need their own sentences\n"
    "- every character is an adult\n"
)


def cmd_story(args):
    premise = (args.premise or "").strip()
    sheet = read_sheet(args.sheet) if args.sheet else None
    look = ", ".join((sheet or {}).get("appearance", [])) or "1girl"
    ask = (
        "Write a %d panel manga script.\n" % args.panels
        + "Premise: %s\n" % (premise or "invent a small, warm everyday moment")
        + "The main character looks like this and must never be contradicted: %s\n" % look
        + "Write the title line first (simplified Chinese characters), then exactly %d body "
          "sentences in story order - count the sentences before you answer."
          % args.panels
    )
    think = bool(getattr(args, "think", False))
    # temperature is the other half of the fix: at 0.5 the model returns the modal (i.e.
    # safest) continuation that still fits the premise, which is how an NTR premise came
    # back as a romance. Wider sampling is what lets the directive actually take effect.
    adult = bool(getattr(args, "adult", False))
    text = chat([{"role": "system", "content": STORY_SYSTEM + ("\n" + ADULT_DIRECTIVE if adult else "")},
                 {"role": "user", "content": ask}],
                max_tokens=tokens_for(700, think),
                temperature=0.85 if adult else 0.5, think=think)
    lines = [l.strip() for l in text.replace("```", "").splitlines() if l.strip()]
    if not lines:
        log("story generation returned nothing")
        return 2
    title = lines[0].lstrip("#").replace("标题：", "").replace("标题:", "").strip()
    body = " ".join(lines[1:]) if len(lines) > 1 else lines[0]
    # the model likes to label beats ("第一-panel:", "Panel 2:") even when told not to;
    # those labels are noise in the prompt the planner reads, so strip them
    body = re.sub(r"第[一二三四五六七八九十百\d]+\s*-?\s*(?:panel|格|幕|画面|页)\s*[：:、.]\s*", "",
                  body, flags=re.I)
    body = re.sub(r"(?i)\bpanel\s*\d+\s*[：:、.]\s*", "", body)
    # The 9B is not reliable about the 。 separator - it tends to write one
    # sentence per line instead. Normalise to one sentence per line, which is
    # exactly what split_beats consumes.
    parts = [s.strip(" 。，、") for s in re.split(r"[。！？!?\n]+", body) if s.strip(" 。，、")]
    raw = len(parts)
    if raw < args.panels:
        # the 9B also separates with plain spaces; fall back to the planner's own
        # beat splitter so the file mirrors the beats it will actually receive
        parts = split_beats("\n".join(parts), args.panels)
    if raw != args.panels:
        log("note: model produced %d sentence(s) for %d panels - normalised to %d beats"
            % (raw, args.panels, len(parts)))
    body = "\n".join(parts)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("# %s\n%s\n" % (title, body), encoding="utf-8")
    audit("story", premise=premise, panels=args.panels, title=title, out=str(out))
    log("title: %s" % title)
    log("story: %s" % (body[:200] + ("..." if len(body) > 200 else "")))
    log("written -> %s" % out)
    return 0


# ---------------------------------------------------------------- plan
def cmd_plan(args):
    story = clean_story(Path(args.story).read_text(encoding="utf-8"))
    if not story:
        log("story file is empty")
        return 1

    total = args.panels
    planned = []
    sheet = read_sheet(args.sheet) if args.sheet else None
    if sheet:
        log("character sheet pinned from %s: %s"
            % (args.sheet, ", ".join(sheet["appearance"][:6])))

    # Preset library: artist tags, ready-made content blocks and negative packs. All
    # resolved once here so every panel in the run shares the same choices.
    artist_tags, preset_tags, preset_texts, negative_override = [], [], [], None
    if args.artist or args.preset or args.negative_pack:
        import presets as presetlib

        if not presetlib.available():
            log("preset library missing - run: python pipeline\\build_presets.py --kit <dir>")
        else:
            if args.artist:
                artist_tags, unknown = presetlib.artist_tags(args.artist)
                for u in unknown:
                    log("artist %r is not in the ranked table - using it as written" % u)
            for want in (args.preset or []):
                hit = presetlib.find_preset(want)
                if not hit:
                    log("no unique preset matches %r - see: python pipeline\\presets.py --list"
                        % want)
                    continue
                preset_tags += [t.strip() for t in hit["tags"].split(",") if t.strip()]
                if hit["text"]:
                    preset_texts.append(hit["text"])
                log("preset %s [%s%s] -> %d tags"
                    % (hit["name"], hit["kind"], ", nsfw" if hit["nsfw"] else "",
                       len(preset_tags)))
            if args.negative_pack:
                pack = presetlib.negative_packs().get(args.negative_pack)
                if pack is None:
                    log("no such negative pack: %r" % args.negative_pack)
                else:
                    # the kit's packs do not carry the anti-artifact block, and losing it
                    # would quietly let pseudo-text back into the artwork
                    from runner import NEG_ARTIFACT

                    negative_override = pack + ", " + NEG_ARTIFACT

    beats = split_beats(story, total)
    if args.auto_preset:
        import presets as presetlib

        if not presetlib.available():
            log("preset library missing - run: python pipeline\\build_presets.py --kit <dir>")
        else:
            story_text = " ".join(beats)
            # The adult pool is opt-in, and that is a deliberate design choice rather than
            # caution: an earlier version asked the model to classify the story's tone and
            # then filtered the menu. It misread a wholesome story as adult, and because
            # the model also refuses to emit explicit preset names (empty picks), the
            # fallback then injected explicit tags into a children-and-kittens beat. Tone
            # is the operator's call, not a guess.
            cand = [p for p in presetlib.search(story_text, 30)
                    if args.auto_preset_adult or not p["nsfw"]][:14]
            picked = choose_presets(story_text, cand, args.auto_preset)
            if not picked and cand:
                # The model declined. Retrieval already ranked these and the pool is
                # code-controlled, so take the single best match rather than doing nothing.
                picked = cand[:1]
                log("auto preset: model declined, falling back to the top-ranked match")
            for p in picked:
                preset_tags += [t.strip() for t in p["tags"].split(",") if t.strip()]
                if p["text"]:
                    preset_texts.append(p["text"])
                log("auto preset %s [%s%s]"
                    % (p["name"], p["kind"], ", adult" if p["nsfw"] else ""))
            if not picked:
                log("auto preset: nothing in the %d-entry shortlist fitted the story"
                    % len(cand))
            audit("auto_preset", shortlist=[p["name"] for p in cand],
                  picked=[p["name"] for p in picked])

    if args.auto_artist:
        import presets as presetlib

        menu = presetlib.artist_menu(60)
        if not menu:
            log("auto artist: no style index yet - run: python pipeline\\build_artist_styles.py")
        else:
            picks = choose_artists(" ".join(beats), menu, args.auto_artist)
            style_of = dict(menu)
            for n in picks:
                artist_tags.append("@" + n)
                log("auto artist @%s : %s" % (n, style_of[n]))
            if not picks:
                log("auto artist: nothing in the %d-artist menu fitted the story" % len(menu))
            audit("auto_artist", menu=[n for n, _ in menu], picked=picks)
    log("story sliced into %d beats" % len(beats))

    # Planning is chunked (CHUNK panels per LLM call), so a 40-page story is ~10 calls.
    # The jobs used to be written only after the LAST one, which meant a failure at chunk
    # 9 threw away every chunk before it - that really happened, and a 40-page run lost all
    # of its planning to it. Each finished chunk is appended to a sidecar file instead, and
    # a re-run resumes at the first chunk that is missing rather than starting over.
    partial = Path(str(args.story)).with_suffix(".plan.partial.jsonl")
    done_chunks = {}
    if partial.exists():
        for line in partial.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            # a partial file from a different story length or mode is not resumable
            if rec.get("total") == total and rec.get("page_mode") == bool(args.page_mode):
                done_chunks[int(rec["start"])] = rec
        if done_chunks:
            log("resuming planning: chunk(s) %s already done, %d beat(s) recovered"
                % (", ".join(str(k) for k in sorted(done_chunks)),
                   sum(len(r.get("panels") or []) for r in done_chunks.values())))

    for start in range(0, total, CHUNK):
        n = min(CHUNK, total - start)
        if start in done_chunks:
            rec = done_chunks[start]
            if sheet is None and rec.get("sheet"):
                sheet = rec["sheet"]
            planned.extend(rec.get("panels") or [])
            continue
        context = ""
        if planned:
            context = ("\nPrevious panels (do not repeat their pose_action tags):\n"
                       + json.dumps([{"shot": p.get("shot"), "pose_action": p.get("pose_action")}
                                     for p in planned[-3:]], ensure_ascii=False))
        beat_list = "\n".join(
            "  Panel %d: %s" % (start + 1 + i, beats[start + i]) for i in range(n)
        )
        intro = ("First define the character sheet.\n" if sheet is None else
                 "Reuse this character sheet verbatim: %s\n" % json.dumps(sheet, ensure_ascii=False))
        if args.page_mode:
            ask = (
                intro
                + "Draw %d manga page(s), numbered %d..%d of %d. Each page is ONE illustration "
                  "that carries its own 2-4 panels; the beat tells you what happens on that page.\n"
                  % (n, start + 1, start + n, total)
                + "Beats:\n" + beat_list
                + context
                + "\nReply with JSON only:\n"
                  '{"character_sheet": {"appearance": ["hair tag", "eye tag", "outfit tag"],\n'
                  '    "props": ["colour + size + kind, e.g. \\"small orange tabby kitten\\""]},\n'
                  ' "pages": [{"id": 1, "panel_count": 2,\n'
                  '   "shot": "wide shot|medium shot|close-up", "scene": "where this beat happens",\n'
                  '   "pose_action": ["4-6 tags for the action inside THIS page"],\n'
                  '   "tags": ["character sheet tags", "location tags", "prop tags"],\n'
                  '   "dialogue": "简体中文台词，例如：你终于回来了"}]}\n'
                  + ("panel_count must be %d on every page." % args.page_panels
                     if args.page_panels else "panel_count must be 2 or 4.")
                  # The example value used to read "没有台词的页面留空字符串" and the model
                  # mirrored it literally - a 40-page run came back with dialogue empty on
                  # every single page. An example is an instruction; this one now shows a
                  # real line, and the escape hatch is stated as the exception it is.
                  + " The beats never quote speech, so invent the line yourself: fill dialogue"
                    " on every page where two characters interact, and use \"\" only on a page"
                    " with a single character and no interlocutor."
            )
        else:
            ask = (
                intro
                + "Visualise panels %d..%d of %d. Each panel must show its own beat - "
                  "different location, pose and action where the beat demands it.\n"
                  % (start + 1, start + n, total)
                + "Beats:\n" + beat_list
                + context
                + "\nReply with JSON only:\n"
                  '{"character_sheet": {"appearance": ["hair tag", "eye tag", "outfit tag"],\n'
                  '    "props": ["colour + size + kind, e.g. \\"small orange tabby kitten\\"", '
                  '"e.g. \\"navy school bag\\""]},\n'
                  ' "panels": [{"id": 1, "shot": "wide shot|medium shot|close-up|over the shoulder",\n'
                  '   "scene": "where this beat happens", "emotion": "one word",\n'
                  '   "pose_action": ["4-6 tags for the pose and action of THIS beat"],\n'
                  '   "tags": ["character sheet tags", "location tags", "prop tags", "expression tag"],\n'
                  '   "dialogue": "简体中文台词"}]}\n'
                  "dialogue must never be empty, and the same prop must look identical in every panel."
            )
        think = getattr(args, "think", False)
        # The planner gets the directive too. It converts beats to danbooru tags 1:1, so
        # explicit beats already carry most of it - but without this an aligned model can
        # still soften the tags on the way through, which would waste the whole fix.
        plan_system = SYSTEM_PAGE if args.page_mode else SYSTEM
        if getattr(args, "adult", False):
            plan_system += "\n" + ADULT_DIRECTIVE
        obj = llm_json([{"role": "system", "content": plan_system},
                        {"role": "user", "content": ask}],
                       max_tokens=tokens_for(1800, think),
                       schema=PAGES_SCHEMA if args.page_mode else PANELS_SCHEMA,
                       think=think)
        if sheet is None:
            sheet = obj.get("character_sheet") or {}
        wanted = "pages" if args.page_mode else "panels"
        chunk = [p for p in obj.get(wanted, [])[:n]]
        planned.extend(chunk)
        # flush this chunk immediately; the whole point is to survive a later chunk failing
        with partial.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"start": start, "total": total, "n": n,
                                 "page_mode": bool(args.page_mode), "sheet": sheet,
                                 "panels": chunk}, ensure_ascii=False) + "\n")
        log("  chunk %d..%d planned (%d/%d)"
            % (start + 1, start + n, len(planned), total))

    if not planned:
        log("planner produced no %s" % ("pages" if args.page_mode else "panels"))
        return 2

    style = None
    if args.style:
        style = read_json(REG, {}).get(args.style)
        if not style:
            log("style '%s' is not registered in %s (deploy it first: "
                "style_train.py deploy --name %s)" % (args.style, REG, args.style))
            return 2
        log("style attached: %s (trigger=%s, strength=%s)"
            % (style.get("lora_file"), style.get("trigger"), style.get("strength", 1.0)))

    jobs = load_jobs()
    idx = next_index(jobs)
    # sheet_bits = the girl's look + the recurring props: both must be repeated verbatim
    sheet_bits = []
    for key in ("appearance", "props"):
        sheet_bits.extend([str(x) for x in ((sheet or {}).get(key) or []) if str(x).strip()])
    appearance = ", ".join(sheet_bits)
    added = []
    for p in planned:
        jid = "p%03d" % idx
        idx += 1

        def norm(t):
            return str(t).strip().lower().replace("_", " ")  # Anima wants spaces, not underscores

        pose = [norm(t) for t in (p.get("pose_action") or []) if str(t).strip()]
        tags = [norm(t) for t in (p.get("tags") or []) if str(t).strip()]
        prompt_tags = []
        for t in pose + tags:  # pose/action first: it is what distinguishes the beat
            if t and t not in prompt_tags:
                prompt_tags.append(t)
        if appearance:
            for a in [norm(x) for x in appearance.split(",") if x.strip()]:
                if a not in prompt_tags:
                    prompt_tags.append(a)
        # A line belongs only to a beat that has one: wordless pages stay wordless,
        # because a stray bubble on a silent page is a rendering bug.
        dialogue = (p.get("dialogue") or "").strip()[:40]
        if args.page_mode:
            # the model draws the panels inside one image, so the layout tags sit
            # right behind the trigger: they steer composition, not identity
            for t in reversed(page_layout_tags(args.page_panels or p.get("panel_count"),
                                               bool(dialogue))):
                if t not in prompt_tags:
                    prompt_tags.insert(0, t)
        if style:
            # the trigger carries identity; the sheet still repeats the variable bits
            # (outfit, props) because those drift across scenes otherwise
            prompt_tags.insert(0, style["trigger"])
        if artist_tags:
            # the model card's tag order puts artist after character/series and before
            # the general tags - exactly behind the trigger
            at = 1 if style else 0
            prompt_tags[at:at] = [t for t in artist_tags if t not in prompt_tags]
        for t in preset_tags:
            if t not in prompt_tags:
                prompt_tags.append(t)
        # The model card asks for detailed natural language next to the tags, and the
        # kits ship one sentence per preset for exactly this slot, so it trails the tags.
        prompt_text = ", ".join(prompt_tags)
        if preset_texts:
            prompt_text += ". " + " ".join(preset_texts)
        job = {
            "id": jid,
            "width": args.width,
            "height": args.height,
            "seed": seed_for(p.get("shot", "") + p.get("scene", "") + jid, jid),
            "turbo": bool(args.draft),
            "steps": 10 if args.draft else 40,
            "cfg": 1.0 if args.draft else 4.0,
            "prompt": prompt_text,
            "tags": prompt_tags[:8],
            "shot": p.get("shot"),
            "scene": p.get("scene"),
            "emotion": p.get("emotion"),
            "dialogue": dialogue,
        }
        if args.page_mode and dialogue:
            # only a page that carries a line gets a blank bubble, so only this job's
            # negative prompt needs speech bubbles let through
            job["allow_bubble"] = True
        if args.hires:
            job["hires"] = float(args.hires)
            job["hires_denoise"] = float(args.hires_denoise)
            job["hires_method"] = args.hires_method
        if negative_override:
            # A job-level negative is used verbatim by runner.py, which would bypass
            # negative_default()'s bubble rule - so mirror it here: a bubble is only
            # allowed on a page that actually carries a line.
            from runner import NEG_BUBBLE

            job["negative"] = (negative_override if job.get("allow_bubble")
                               else negative_override + NEG_BUBBLE)
        if style:
            job["style_lora"] = style["lora_file"]
            job["style_strength"] = float(style.get("strength", 1.0))
        if args.ref_img:
            # the in-context nodes take two references at most (full body + face close-up
            # works best); runner.py copies them into ComfyUI's input dir on demand
            job["ref_images"] = [str(x) for x in args.ref_img[:2]]
            job["incontext_strength"] = args.ref_strength
        jobs.append(job)
        added.append(jid)

    save_jobs(jobs)
    audit("plan", story=args.story, panels=len(added), ids=added,
          draft=bool(args.draft), style=args.style, character_sheet=sheet)
    log("planned %d panels -> %s" % (len(added), ", ".join(added)))
    log("character sheet: %s" % (appearance or "(none)"))
    # jobs.jsonl is on disk now, so the resumable sidecar has done its job
    partial.unlink(missing_ok=True)
    return 0


# ---------------------------------------------------------------- review
def cmd_review(args):
    qc = read_json(QC, {})
    state = read_json(STATE, {})
    jobs = load_jobs()
    by_id = {j["id"]: j for j in jobs}

    rerolls, drops = [], []
    for jid, rec in qc.items():
        job = by_id.get(jid)
        if not job:
            continue
        # Not every id in the queue is a panel: verify_style.py writes v01, v01s10 and
        # friends, and qc.json covers them too. This line used to call .group(1) on the
        # match unconditionally, so one non-panel id crashed the whole review pass - and
        # because the stage wrapper logs the failure as ok, the effect was that QC
        # failures silently stopped being requeued. Skip them instead.
        m = re.match(r"(p\d+)", str(jid))
        if not m:
            continue
        base_id = m.group(1)
        gen = 0
        for j in jobs:
            if str(j["id"]).startswith(base_id + "r"):
                gen = max(gen, int(str(j["id"]).split("r")[-1]))
        if rec.get("status") == "pass":
            continue
        if gen >= args.max_reroll:
            drops.append((jid, rec.get("reasons")))
            audit("drop", job=jid, reasons=rec.get("reasons"), generations=gen)
            continue
        new_id = "%sr%d" % (base_id, gen + 1)
        new_job = dict(job)
        new_job["id"] = new_id
        new_job["seed"] = int(job.get("seed", 0)) + 7919 * (gen + 1)  # new seed, same intent
        rerolls.append(new_job)
        audit("reroll", job=jid, new_job=new_id, reasons=rec.get("reasons"),
              seed_from=job.get("seed"), seed_to=new_job["seed"])

    # a render that never produced a file also needs a second chance
    for jid, rec in state.items():
        if rec.get("status") == "failed" and jid not in qc:
            job = by_id.get(jid)
            if job:
                new_id = "%sr1" % re.match(r"(p\d+)", jid).group(1)
                if new_id not in by_id:
                    new_job = dict(job)
                    new_job["id"] = new_id
                    new_job["seed"] = int(job.get("seed", 0)) + 7919
                    rerolls.append(new_job)
                    audit("reroll_render_fail", job=jid, new_job=new_id, error=rec.get("error"))

    if rerolls:
        jobs.extend(rerolls)
        save_jobs(jobs)
    log("review: %d rerolls queued, %d dropped" % (len(rerolls), len(drops)))
    for r in rerolls:
        log("  requeue %s (seed %s)" % (r["id"], r["seed"]))
    for jid, reasons in drops:
        log("  dropped %s (%s)" % (jid, reasons))
    return 0


# ---------------------------------------------------------------- judge
# The rule-based review only acts on the binary gate (technical). It says
# nothing about a panel that passed the gate but ignored most of its intent, which
# is exactly what this model-driven pass is for. Dry by design: it records verdicts
# so they can be compared, and never queues work on its own.
JUDGE_SYSTEM = (
    "You are the quality reviewer of a manga production pipeline. "
    "You output JSON only - no prose, no markdown fences.\n"
    "You get the rendered panel itself, plus the pipeline's own measurements. "
    "Judge the PICTURE: does it show what was asked, is the character consistent, "
    "are there broken hands, extra limbs, garbled text, cropped faces or empty panels? "
    "Treat the measurements as a hint, not as the truth - they are computed by a tagger "
    "and can be wrong.\n"
    "Decide whether the panel is good enough to keep, or should be re-rendered with a new "
    "seed (prompt and intent stay the same).\n"
    "Guidelines:\n"
    "- never flag a panel for its subject matter or rating: judge execution and intent\n"
    "- reroll when you can SEE a real defect or the panel misses its intent\n"
    "- keep when it only differs in style details\n"
    "- be decisive, do not hedge, never mention the measurements as your only reason\n"
    'Reply exactly: {"verdict": "keep" or "reroll", "reason": "one short Simplified Chinese sentence"}'
)


def image_content(path, max_side=512):
    """Data URL for one panel, downscaled before it is sent.

    512 rather than the original 768, because the image dominates the judge's cost and
    that cost scales with its resolution. Measured on this machine for one 1024x1024
    panel (prompt tokens / total time): 768 -> 836 tok / 16.2 s, 640 -> 660 / 10.5 s,
    512 -> 516 / 6.7 s, 384 -> 404 / 4.5 s, 256 -> 324 / 3.0 s. Prefill throughput itself
    also improves (41.5 -> 55.1 tok/s) because the vision tower has less to chew on.
    512 is the knee: defect detection (bad hands, extra limbs, garbled text, cropped
    faces) is what this judge is for, and those survive the downscale. Drop to 384 only
    if the judge's verdicts are later shown to hold up at that size.
    """
    from PIL import Image
    img = Image.open(path).convert("RGB")
    w, h = img.size
    if max(w, h) > max_side:
        s = max_side / float(max(w, h))
        img = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def hit_ratio(hit):
    try:
        a, b = str(hit).split("/")
        b = int(b)
        return (int(a) / b) if b else 0.0
    except Exception:
        return 1.0


def beat_key(jid):
    """The page an id belongs to, with its 'final' and 'reroll' markers stripped.

    Ids run p008 (draft) -> p008f (final) -> p008r1f (the judge's second attempt at that
    final). Two callers need this: the judge, to recognise a beat it has already given a
    second chance, and publish_pages, to ship one image per beat instead of one per id.
    """
    s = str(jid)
    if not s.endswith("f"):
        return s
    s = s[:-1]
    i = s.rfind("r")
    if i > 0 and s[i + 1:].isdigit():
        s = s[:i]
    return s


AUTO_ARTIST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"picks": {"type": "array", "maxItems": 4,
                             "items": {"type": "string", "maxLength": 60}}},
    "required": ["picks"],
}


def choose_artists(story, menu, want):
    """Pick artists off a menu of (name, style keywords); keep only exact menu names.

    The menu is the point: an artist name on its own tells the model nothing, so the
    vision-built style index is what makes this question answerable at all.
    """
    if not menu or want <= 0:
        return []
    text = "\n".join("- %s: %s" % (n, s) for n, s in menu)
    ask = ("Story:\n%s\n\nPick at most %d artists from the menu whose STYLE fits this "
           "story's mood and subject. Use the names exactly as written and never invent "
           "one. Pick fewer - even none - if nothing fits.\n"
           "Reply with JSON only.\n\nMenu:\n%s" % (story[:1200], want, text))
    try:
        obj = llm_json([{"role": "system", "content":
                         "You match a story to a fixed menu of illustrators by art style. "
                         "You output JSON only and never use a name not in the menu."},
                        {"role": "user", "content": ask}],
                       max_tokens=140, schema=AUTO_ARTIST_SCHEMA)
    except Exception as e:
        log("auto artist: model unavailable (%s) - continuing with none" % str(e)[:90])
        return []
    known = {n for n, _ in menu}
    out = []
    for n in (obj.get("picks") or [])[:want]:
        k = str(n).strip().lstrip("@")
        if k in known and k not in out:
            out.append(k)
    return out


def cmd_judge(args):
    qc = read_json(QC, {})
    jobs = load_jobs()
    by_id = {j["id"]: j for j in jobs}

    cands = []
    for jid, rec in qc.items():
        job = by_id.get(jid)
        if not job or rec.get("status") != "pass" or not rec.get("tag_hit"):
            continue
        cands.append((hit_ratio(rec["tag_hit"]), jid, job, rec))
    # Newest first, then worst coverage. Sorting by coverage alone was fine while the
    # queue was small, but on a mature queue the whole judge budget flowed to panels from
    # earlier projects: measured on a one-page test run, it reviewed p002/p006/p008/p012
    # and never looked at the page that run had just produced. A fresh batch has to be
    # what gets reviewed, or the supervision is spent on work that already shipped.
    cands.sort(key=lambda c: (-int(re.sub(r"\D", "", c[1]) or 0), c[0]))
    picked = cands[:args.limit]
    if not picked:
        log("judge: nothing to review (need passed panels that carry a tag_hit)")
        return 0
    log("judge: reviewing %d of %d passed panels (worst intent coverage first)"
        % (len(picked), len(cands)))

    verdicts = []
    for ratio, jid, job, rec in picked:
        ask = (
            "Panel: %s\n" % jid
            + "Intended prompt: %s\n" % job.get("prompt", "")
            + "Intended tags: %s\n" % ", ".join(job.get("tags") or [])
            + "Detected by the WD tagger: %s\n" % ", ".join((rec.get("tags_detected") or [])[:22])
            + "Intent coverage: %s intended tags were detected\n" % rec.get("tag_hit")
            + "Rating (tagger, informational only): %s (general=%.3f)\n"
              % (rec.get("rating"), (rec.get("rating_scores") or {}).get("general", 0.0))
            + "Technical: %s\n" % ("ok" if (rec.get("technical") or {}).get("ok") else "problems")
            + "Other signals: %s\n" % ", ".join(rec.get("reasons") or ["none"])
            + "\nKeep this panel, or re-render it with a new seed? Reply JSON only."
        )
        # Thinking is reserved for panels that visibly missed their intent: at
        # ~4.5 tok/s a thinking judge call costs minutes, so it has to earn its keep.
        think = bool(getattr(args, "think", False)) and ratio < args.think_below

        # with --vision the model looks at the actual PNG; without it, only numbers
        content = ask
        img_path = rec.get("file")
        attached = False
        if args.vision and img_path and Path(img_path).exists():
            try:
                content = [{"type": "text", "text": ask},
                           {"type": "image_url", "image_url": {"url": image_content(img_path)}}]
                attached = True
            except Exception as e:
                log("  %s could not attach the image: %s" % (jid, e))
        t0 = time.time()
        try:
            obj = llm_json([{"role": "system", "content": JUDGE_SYSTEM},
                            {"role": "user", "content": content}],
                           max_tokens=tokens_for(220, think), tries=2,
                           schema=JUDGE_SCHEMA, think=think)
        except Exception as e:
            log("  %s judge call failed: %s" % (jid, e))
            continue
        verdict = str(obj.get("verdict", "")).strip().lower()
        if verdict not in ("keep", "reroll"):
            log("  %s invalid verdict %r - skipped" % (jid, obj.get("verdict")))
            continue
        reason = str(obj.get("reason", "")).strip()[:140]
        dt = time.time() - t0
        verdicts.append((jid, verdict, reason, dt))
        audit("judge", job=jid, verdict=verdict, reason=reason, tag_hit=rec.get("tag_hit"),
              vision=attached, think=think, seconds=round(dt, 1))
        log("  %-6s hit=%-5s vision=%-5s think=%-5s -> %-6s (%.1fs) %s"
            % (jid, rec.get("tag_hit"), attached, think, verdict, dt, reason))

    n_re = sum(1 for _, v, _, _ in verdicts if v == "reroll")
    avg = (sum(d for *_, d in verdicts) / len(verdicts)) if verdicts else 0.0
    log("judge done: %d keep, %d reroll, %.1fs per panel" % (len(verdicts) - n_re, n_re, avg))

    # Closing the loop. This pass used to be dry: verdicts only ever reached audit.jsonl.
    # A "reroll" verdict can now queue a replacement render, bounded twice over - one
    # second chance per beat, and at most --reroll-max per run - because an unbounded
    # loop here would spend GPU time on a judge's opinion indefinitely. The reroll is a
    # full final-quality render of the same intent with a new seed, and it carries
    # judge_reroll so the next pass can see the beat has already had its second chance.
    queued = []
    if args.reroll and n_re:
        jobs = load_jobs()
        by_id = {j["id"]: j for j in jobs}
        spent = {beat_key(j["id"]) for j in jobs if j.get("judge_reroll")}
        for jid, verdict, reason, _ in verdicts:
            if verdict != "reroll":
                continue
            if len(queued) >= args.reroll_max:
                log("  reroll budget of %d reached - the rest stay as they are"
                    % args.reroll_max)
                break
            src = by_id.get(jid)
            if not src:
                continue
            key = beat_key(jid)
            if key in spent:
                log("  %s already had its second chance - not rerolling again" % jid)
                continue
            spent.add(key)
            nj = dict(src)
            nj["id"] = key + "r1f"
            nj["seed"] = int(src.get("seed", 0)) + 7919
            nj["turbo"] = False
            nj["steps"] = max(int(src.get("steps", 40)), 40)
            nj["judge_reroll"] = 1
            queued.append((nj, jid, reason))
        if queued:
            jobs.extend(nj for nj, _, _ in queued)
            save_jobs(jobs)
            for nj, src_id, reason in queued:
                audit("judge_reroll", job=nj["id"], source=src_id, seed=nj["seed"],
                      reason=reason)
                log("  queued reroll %s (seed %s) for %s" % (nj["id"], nj["seed"], src_id))
        else:
            log("  no reroll queued")
    elif n_re and not args.reroll:
        log("  %d reroll verdict(s) not acted on - pass --reroll to queue them" % n_re)
    return 0


# ---------------------------------------------------------------- finalize
def cmd_finalize(args):
    """Draft -> final: same seed and intent, full 30-step / CFG 4 render."""
    jobs = load_jobs()
    state = read_json(STATE, {})
    by_id = {j["id"]: j for j in jobs}
    done = {jid for jid, r in state.items() if r.get("status") == "done"}

    if args.ids:
        targets = [by_id[i] for i in args.ids if i in by_id and i in done]
    else:
        targets = [j for j in jobs if j.get("turbo") and j["id"] in done]

    added = []
    for j in targets:
        new_id = j["id"] + "f"
        if new_id in by_id:
            continue
        nj = dict(j)
        nj["id"] = new_id
        nj["turbo"] = False
        nj["steps"] = 40
        nj["cfg"] = 4.0
        if args.hires:
            # the refine pass costs real time, so it belongs to the finals and only when
            # asked for; a draft that already carries hires keeps it otherwise
            nj["hires"] = float(args.hires)
            nj["hires_denoise"] = float(args.hires_denoise)
            nj["hires_method"] = args.hires_method
        jobs.append(nj)
        added.append(new_id)

    if added:
        save_jobs(jobs)
    audit("finalize", ids=added, source=[j["id"] for j in targets])
    log("finalize: queued %d final renders (same seeds as drafts)" % len(added))
    for a in added:
        log("  %s" % a)
    return 0


# ---------------------------------------------------------------- control / status
def cmd_control(args):
    ctl = read_json(CONTROL, {})
    if args.mode:
        ctl["mode"] = args.mode
    if args.reroll:
        ctl.setdefault("reroll", []).extend(args.reroll)
    write_json(CONTROL, ctl)
    audit("control", mode=ctl.get("mode"), reroll=ctl.get("reroll"))
    log("control.json = %s" % json.dumps(ctl, ensure_ascii=False))
    return 0


def cmd_status(_args):
    jobs = load_jobs()
    state = read_json(STATE, {})
    qc = read_json(QC, {})
    ctl = read_json(CONTROL, {})
    print("control: %s" % json.dumps(ctl, ensure_ascii=False))
    print("%-10s %-10s %-12s %s" % ("job", "render", "qc", "dialogue"))
    for j in jobs:
        jid = j["id"]
        print("%-10s %-10s %-12s %s" % (
            jid, state.get(jid, {}).get("status", "-"),
            qc.get(jid, {}).get("status", "-"), (j.get("dialogue") or "")[:20]))
    done = sum(1 for j in jobs if state.get(j["id"], {}).get("status") == "done")
    print("total=%d rendered=%d" % (len(jobs), done))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p0 = sub.add_parser("story", help="let the local model write the story itself")
    p0.add_argument("--premise", default="", help="one line premise; empty = invent one")
    p0.add_argument("--panels", type=int, default=8)
    p0.add_argument("--out", default=str(ROOT / "workspace" / "jobs" / "story.txt"))
    p0.add_argument("--sheet", default=None, help="pinned character sheet file")
    p0.add_argument("--think", action="store_true",
                    help="reason before answering (slower; see README)")
    p0.add_argument("--adult", action="store_true",
                    help="the premise is adult material: the beats must carry the physical "
                         "escalation instead of describing only mood. Opt-in on purpose - "
                         "tone is the operator's call, not the model's guess")
    p0.set_defaults(func=cmd_story)

    p1 = sub.add_parser("plan")
    p1.add_argument("--story", required=True)
    p1.add_argument("--panels", type=int, default=8)
    p1.add_argument("--width", type=int, default=1024)
    p1.add_argument("--height", type=int, default=1024)
    p1.add_argument("--draft", action="store_true", help="plan as turbo drafts (fast)")
    p1.add_argument("--style", default=None,
                    help="style name from styles.json: attaches the trained LoRA + trigger word")
    p1.add_argument("--sheet", default=None,
                    help="pinned character sheet file: the model reuses it instead of inventing one")
    p1.add_argument("--page-mode", action=argparse.BooleanOptionalAction, default=True,
                    help="one image = one finished manga page (default on; the dialogue "
                         "is written into the page's blank bubble by compose.py)")
    p1.add_argument("--page-panels", type=int, choices=[2, 4], default=None,
                    help="force the panel count on every page (2koma / 4koma)")
    p1.add_argument("--think", action="store_true",
                    help="reason before answering (slower; see README)")
    p1.add_argument("--ref-img", action="append", default=None,
                    help="character reference image (repeatable, max 2 used): every panel "
                         "gets it attached through the Anima In-Context nodes")
    p1.add_argument("--ref-strength", type=float, default=1.0,
                    help="attention pull toward the reference images (1.0 neutral, "
                         "1.2-1.5 when identity drifts)")
    p1.add_argument("--hires", type=float, default=None, metavar="SCALE",
                    help="add a latent-upscale refine pass at this scale (e.g. 1.5, which "
                         "takes 1024x1024 to 1536x1536). Costs roughly one extra render")
    p1.add_argument("--hires-method", default="nearest-exact",
                    choices=["nearest-exact", "bislerp", "bilinear", "bicubic", "area"],
                    help="latent upscale used by the refine pass. nearest-exact is blocky; "
                         "bislerp is the smoother community default - A/B them on one seed")
    p1.add_argument("--hires-denoise", type=float, default=0.25,
                    help="denoise for the refine pass. Measured here: 0.40 re-drew the "
                         "hands, 0.25 kept them intact and still added real detail")
    p1.add_argument("--artist", action="append", default=None, metavar="NAME",
                    help="@artist style tags (repeatable). The model card calls this the "
                         "strongest style lever; browse: presets.py --artists 40")
    p1.add_argument("--preset", action="append", default=None, metavar="NAME",
                    help="pin a ready-made tag block plus its natural-language sentence "
                         "(repeatable). Browse: presets.py --list [--nsfw]")
    p1.add_argument("--negative-pack", default=None, metavar="NAME",
                    help="use a vetted negative pack instead of the built-in default; the "
                         "anti-artifact block is still appended. See presets.py --negatives")
    p1.add_argument("--auto-preset", type=int, default=0, metavar="N",
                    help="let the model pick up to N presets for this story from a shortlist "
                         "retrieved off the story text (0 = off). Picks are validated "
                         "against the library, so a bad pick degrades to no preset. Only "
                         "non-adult presets are eligible unless --auto-preset-adult is given")
    p1.add_argument("--auto-preset-adult", action="store_true",
                    help="also let --auto-preset draw from the adult presets. Opt-in on "
                         "purpose: the tone of a run is the operator's call, not a guess")
    p1.add_argument("--adult", action="store_true",
                    help="plan the story as adult material: the beats are physical and the "
                         "tags must render them as such rather than softening them. Same "
                         "opt-in principle as --auto-preset-adult")
    p1.add_argument("--auto-artist", type=int, default=0, metavar="N",
                    help="let the model pick up to N @artists for this story from a menu "
                         "of name + style keywords, built by build_artist_styles.py "
                         "(0 = off). Names are validated against the menu, so a bad pick "
                         "degrades to no artist")
    p1.set_defaults(func=cmd_plan)

    p2 = sub.add_parser("review")
    p2.add_argument("--max-reroll", type=int, default=2)
    p2.set_defaults(func=cmd_review)

    p3 = sub.add_parser("control")
    p3.add_argument("--mode", choices=["run", "pause", "stop"])
    p3.add_argument("--reroll", nargs="*")
    p3.set_defaults(func=cmd_control)

    p5 = sub.add_parser("finalize", help="queue full-quality renders of finished drafts")
    p5.add_argument("--ids", nargs="*")
    p5.add_argument("--hires", type=float, default=None, metavar="SCALE",
                    help="add a latent-upscale refine pass at this scale (e.g. 1.5)")
    p5.add_argument("--hires-method", default="nearest-exact",
                    choices=["nearest-exact", "bislerp", "bilinear", "bicubic", "area"])
    p5.add_argument("--hires-denoise", type=float, default=0.25)
    p5.set_defaults(func=cmd_finalize)

    p6 = sub.add_parser("judge", help="model-driven panel review; --reroll closes the loop")
    p6.add_argument("--limit", type=int, default=6,
                    help="how many panels to review, worst intent coverage first")
    p6.add_argument("--reroll", action="store_true",
                    help="act on 'reroll' verdicts by queueing a replacement final render "
                         "of the same intent with a new seed. Off by default so a bad "
                         "judge cannot spend GPU time on its own opinion")
    p6.add_argument("--reroll-max", type=int, default=4,
                    help="cap on rerolls queued by one judge pass (default 4). Each beat "
                         "gets at most one second chance regardless of this number")
    p6.add_argument("--vision", action="store_true",
                    help="attach the rendered PNG so the model judges the picture "
                         "(the server must be running with --mmproj)")
    p6.add_argument("--think", action="store_true",
                    help="reason before answering (slower; see README)")
    p6.add_argument("--think-below", type=float, default=0.5,
                    help="only spend thinking tokens on panels whose intent coverage "
                         "is below this ratio (default 0.5 = worse than half; 1.0 = "
                         "always think)")
    p6.set_defaults(func=cmd_judge)

    p4 = sub.add_parser("status")
    p4.set_defaults(func=cmd_status)

    args = ap.parse_args()
    if getattr(args, "think", False):
        # the largest request any caller makes is plan's 1800 + THINK_BUDGET
        warn_if_ctx_tight(tokens_for(1800, True))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())