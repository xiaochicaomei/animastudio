"""Minimal ComfyUI API client for AnimaStudio.

Stdlib only (urllib) so it runs on ComfyUI's embedded python without extra deps.
"""
import json
import time
import urllib.error
import urllib.request


class ComfyError(RuntimeError):
    pass


class Comfy:
    def __init__(self, base="http://127.0.0.1:8188", timeout=60):
        self.base = base.rstrip("/")
        self.timeout = timeout

    # ---------- low level ----------
    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise ComfyError("HTTP %s on %s: %s" % (e.code, path, body[:1500])) from None

    # ---------- api ----------
    def ready(self):
        try:
            self._get("/system_stats")
            return True
        except Exception:
            return False

    def stats(self):
        return self._get("/system_stats")

    def missing_nodes(self, names):
        """Which of `names` this ComfyUI does not have installed.

        Worth checking before building a graph: a job that references a custom node
        that is not installed fails at submit time with a node-type error, which reads
        like a rendering bug rather than a missing dependency. /object_info is large,
        so callers should cache the answer.
        """
        info = self._get("/object_info")
        return [n for n in names if n not in info]

    def vram_free(self):
        d = self.stats()["devices"][0]
        return d.get("vram_free"), d.get("vram_total")

    def free(self, unload_models=True, free_memory=True):
        # ComfyUI answers /free with an empty body, so do not try to parse JSON here
        data = json.dumps({"unload_models": unload_models, "free_memory": free_memory}).encode("utf-8")
        req = urllib.request.Request(
            self.base + "/free", data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            body = r.read()
        return body

    def submit(self, workflow, client_id="animastudio"):
        # ComfyUI's validate_prompt calls .get() on every top-level entry, so any
        # non-node key (e.g. a comment) causes an HTTP 500. Keep only real nodes.
        nodes = {
            k: v
            for k, v in workflow.items()
            if isinstance(v, dict) and "class_type" in v
        }
        res = self._post("/prompt", {"prompt": nodes, "client_id": client_id})
        if "prompt_id" not in res:
            raise ComfyError("submit failed: " + json.dumps(res, ensure_ascii=False)[:1500])
        return res["prompt_id"]

    def wait(self, prompt_id, poll=1.5, timeout=1800, on_tick=None):
        """Wait for a prompt. Returns (entry, min_free_vram)."""
        t0 = time.time()
        min_free = None
        while time.time() - t0 < timeout:
            history = self._get("/history/" + prompt_id)
            if prompt_id in history:
                return history[prompt_id], min_free
            try:
                free, _total = self.vram_free()
                if free is not None:
                    min_free = free if min_free is None else min(min_free, free)
            except Exception:
                pass
            if on_tick:
                on_tick(time.time() - t0)
            time.sleep(poll)
        raise ComfyError("timeout waiting for prompt %s" % prompt_id)

    @staticmethod
    def status_str(entry):
        return ((entry or {}).get("status") or {}).get("status_str")

    @staticmethod
    def error_messages(entry):
        msgs = []
        for m in ((entry or {}).get("status") or {}).get("messages") or []:
            if isinstance(m, (list, tuple)) and len(m) > 1:
                name, payload = m[0], m[1]
                if name in ("execution_error", "execution_interrupted"):
                    msgs.append(json.dumps(payload, ensure_ascii=False)[:1500])
        return msgs

    @staticmethod
    def output_images(entry):
        out = []
        for _node_id, node_out in ((entry or {}).get("outputs") or {}).items():
            for img in node_out.get("images") or []:
                out.append(img)  # {filename, subfolder, type}
        return out