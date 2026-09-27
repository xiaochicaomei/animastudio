# AnimaStudio

**One sentence in, a lettered multi-page manga out — entirely offline.**

A file-contract pipeline that runs on a single machine with no API keys and no cloud
services. A local 9B model does the writing, planning, review and judging; ComfyUI
renders the art; the code types the dialogue into the bubbles.

> 中文完整文档见 [`README.txt`](README.txt)（27 KB，含安装、模型下载与全部设计取舍）。
> **上手看 [`USAGE.md`](USAGE.md)** —— 日常怎么用、每个参数什么效果、踩过的坑都在那里。

---

## Why it exists

The interesting problem is not "call an image model". It is that a comic needs a dozen
decisions to agree with each other — who is in frame, what they wear, which way they
face, what they say, where the bubble goes — and every one of them is made by a
different component that cannot see the others. Most of this repository is the
scaffolding that keeps those decisions consistent, plus the hard-won constants that
came out of measuring where they break.

## The three legs

| Component | Role |
|---|---|
| **llama.cpp** (`llama-server`, port 8080) | Qwen3.5-9B + vision projector. Story, panel plan, visual review, judge. |
| **ComfyUI** (port 8188) | Renders Anima (a Qwen-Image-family DiT) graphs. |
| **kohya `sd-scripts`** | Trains character and style LoRAs against the local base checkpoints. |

They cannot share the 8 GB card, so `orchestrate.py` hands the GPU back and forth
between the agent and the renderer, and a 9B that keeps its weights resident is stopped
rather than unloaded — llama.cpp has no unload endpoint (`/slots/0?action=sleep` → 501,
`/sleep` → 404).

## Pipeline

```
premise ──► agent story ──► agent plan ──► render drafts ──► qc
                                                             │
              ┌──────────────────────────────────────────────┘
              ▼
        agent review ──► agent finalize ──► render finals ──► agent judge
                                                             │
                                                             ▼
                                                    compose pages (+ lettering)
```

Everything is a file under `workspace/jobs/` — `jobs.jsonl`, `state.json`, `qc.json`,
`audit.jsonl` — so any stage can be inspected, resumed, or re-run on its own.

### Modules

| File | Responsibility |
|---|---|
| `agent.py` | Story → panel plan → vision review → judge. Schema-constrained decoding. |
| `runner.py` | ComfyUI graph builder; base-model routing; hires; LoRA attachment. |
| `orchestrate.py` | Stage driver, GPU handoff, resume, page publishing. |
| `compose.py` | Page and grid layout, speech-bubble drawing, text erasure. |
| `text_detect.py` | Finds drawn-in lettering so it can be cleared before real dialogue goes on. |
| `qc.py` | Quality gate (text classes, artifacts). |
| `style_train.py` | kohya wrapper: prep → train → probe → deploy. |

## Constraints this encodes

Each of these was measured on the hardware, not assumed. They are the reason several
functions look paranoid.

- **Anima-2.9B is a 40-block DiT; the 2B family is 28.** A 2B LoRA loads onto the
  40-block model *without error* and silently lands on the wrong layers. Adapters are
  therefore routed by a recorded base model, never inferred. `adapter_family()` is the
  guard, and `anima_utils.count_dit_blocks()` infers the architecture from the
  checkpoint header.
- **A 2.9B hires pass fits in 8 GB — but only with the memory flags.**
  `--enable-dynamic-vram --async-offload --vram-headroom 0.5` take it from "cannot fit"
  to a comfortable 1 GiB spare. Without them the same graph OOMs.
- **The GPU handoff must read `nvidia-smi`, not ComfyUI's own free-VRAM report.** The
  two disagree badly: ComfyUI reported 6.3 GiB free while `nvidia-smi` showed 184 MiB,
  because torch keeps its reserved blocks. Reading the wrong one drops the LLM to a CPU
  build and makes every stage ~9× slower, with one line in the log to show for it.
- **Under constrained decoding the schema is the hard constraint, not the prompt.** A
  field missing from the JSON schema cannot be emitted by the model at all, no matter
  how the prompt is worded — `additionalProperties: false` makes that structural.
- **`runner.py` renders every pending job.** Leftovers from a paused run are re-rendered
  at full cost with the style they were planned with, then discarded at publish time.
  Hence `--clean-queue` and the startup warning.

## Layout

```
pipeline/      the pipeline (this is the project)
tools/         install / fetch / start / stop scripts (PowerShell)
patches/       anima_utils.py as shipped into the kohya fork, kept because trainer/ is not committed
_stage/        scratch: ablations, one-off measurements, analysis notes
workspace/     runtime only - contracts, renders, logs (generated, not committed)
```

## What is deliberately not in this repository

`.gitignore` excludes these for reasons that are not just size:

- **Weights and runtimes** (~45 GB checkout): three Anima checkpoints at 4–5.6 GB each,
  a 5.4 GB GGUF, embedded Python, the kohya venv, CUDA wheels. Fetched by `tools/fetch*.ps1`.
- **Reference artwork** (`workspace/refs/`): third-party illustration collected locally
  to train style LoRAs from. Redistributing it would infringe the artists' copyright.
- **Generated output** (`workspace/out/`): regenerated by running the pipeline.
- **Vendored ComfyUI node packs** (`_stage/promptkit/`, `_stage/anima_incontext/`): they
  ship no LICENSE file, and no license means all rights reserved. Install from upstream.

## Requirements

Windows, one NVIDIA GPU with ≥8 GB VRAM, ~50 GB disk. See `README.txt` for the full
setup sequence; `tools/install.ps1` and `tools/fetch.ps1` automate the downloads.

## Status

Working end to end. A 40-page run takes about an hour of rendering plus several minutes
of agent time; style LoRA training is the long pole at ~7 hours for 1200 steps.
