"""Smoke test for the local agent LLM (llama-server, CPU only).

Verifies: reachability, CPU-only operation, and strict-JSON discipline
(the agent pipeline depends on machine-parseable output).
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8080"


def _get(path, timeout=30):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def chat(messages, max_tokens=1800, temperature=0.6):
    payload = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
        # Qwen3 is a hybrid-thinking model: thinking tokens would eat the budget
        # and break JSON-only output, so disable it for pipeline calls.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read().decode("utf-8"))


def strip_fence(text):
    s = text.strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 2:
            s = parts[1]
            if s.lstrip().startswith("json"):
                s = s.lstrip()[4:]
    return s.strip()


def main():
    try:
        m = _get("/v1/models")
        print("models:", [x.get("id") for x in m.get("data", [])])
    except Exception as e:
        print("AGENT NOT REACHABLE:", type(e).__name__, e)
        return 2

    sys_p = "You are a manga panel planner. Reply with valid JSON only. No prose, no markdown fences."
    usr = (
        "Return a JSON object with key 'panels' (array of 2 items). Each item: "
        "id (int), shot (string), characters (array of string), scene (string), "
        "emotion (string), tags (array of 8 lowercase danbooru-style tags), "
        "dialogue (string, in Chinese).\n"
        "Story: a girl finds a small cat in the schoolyard and decides to keep it a secret."
    )

    t0 = time.time()
    res = chat(
        [{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
        max_tokens=700,
    )
    dt = time.time() - t0

    txt = res["choices"][0]["message"]["content"]
    usage = res.get("usage", {}) or {}
    comp = usage.get("completion_tokens") or 0
    print("elapsed: %.1fs | usage=%s" % (dt, usage))
    if comp:
        print("generation speed: %.2f tok/s (CPU only)" % (comp / dt))

    body = strip_fence(txt)
    try:
        obj = json.loads(body)
        panels = obj.get("panels", [])
        print("JSON PARSE OK | panels=%d" % len(panels))
        print("sample panel:", json.dumps(panels[0], ensure_ascii=False) if panels else "none")
        return 0
    except Exception as e:
        print("JSON PARSE FAILED:", e)
        print("--- raw output (first 900 chars) ---")
        print(txt[:900])
        return 3


if __name__ == "__main__":
    raise SystemExit(main())