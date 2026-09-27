"""Validate safetensors files: parse header, count tensors, report dtype spread."""
import sys

from safetensors import safe_open


def main(paths):
    bad = 0
    for p in paths:
        try:
            with safe_open(p, framework="pt") as f:
                keys = list(f.keys())
                meta = f.metadata() or {}
                dtypes = {}
                for k in keys[:400]:
                    try:
                        d = str(f.get_slice(k).get_dtype())
                        dtypes[d] = dtypes.get(d, 0) + 1
                    except Exception:
                        pass
                name = p.replace("\\", "/").split("/")[-1]
                print("OK   %-34s tensors=%-6d dtypes=%s meta_keys=%s"
                      % (name, len(keys), dtypes, sorted(meta.keys())[:6]))
        except Exception as e:
            bad += 1
            print("BAD  %s -> %s: %s" % (p, type(e).__name__, e))
    print("checked=%d bad=%d" % (len(paths), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))