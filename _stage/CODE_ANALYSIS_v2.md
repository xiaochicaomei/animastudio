# AnimaStudio 源码分析（v2 · 逐行核对版）

> 范围：`pipeline/` 13 个 Python、`tools/` 10 个 PowerShell、根目录 4 个 .bat、`README.txt`、
> `_stage/extra_model_paths.yaml`、`trainer/configs/*.toml`、`pipeline/templates/anima_t2i.json`、
> `pipeline/references/*`、`workspace/jobs/*`（jobs/state/qc/audit/styles/sheet/story）。
> 关键结论都用仓库内代码交叉验证过（含 sd-scripts 内部实现），下文标注了行号。
> v1（`CODE_ANALYSIS.md`）的结论基本成立；本版更正 3 处、新增 9 项实测风险。

---

## 变更记录

**v2.1 —— 移除内容评级闸门（训练 + 出图均无限制）**

改动清单（均已验证）：

| 文件 | 改动 |
|------|------|
| `pipeline/qc.py` | 删除 `ALLOWED_RATINGS` 与 `rating_gate:*` 失败原因；硬失败从 4 类降为 3 类（`blank_or_flat`/`size_mismatch`/`decode_failed`）。评级仍写入 `qc.json`（`rating`/`rating_scores`）**仅作参考** |
| `pipeline/style_train.py` | `prep` 删除评级筛选与 `_rejected` 搬迁逻辑，目录内素材全部参与训练；`_meta.json` 去掉 `rejected` 字段 |
| `pipeline/runner.py` | `POS_PREFIX` 去掉结尾的 `safe, `（原来每格提示词都被硬塞安全评级标签） |
| `pipeline/agent.py` | `JUDGE_SYSTEM` 删除"评级非 general 即重跑"并改为明确的"不得因题材/评级判缺陷"；`STORY_SYSTEM` 删除"温和日常、禁暴力/恋爱/福利"的内容禁令；judge 提示词里的字段标签改为 informational |
| `pipeline/wd_tagger.py` | 文档说明由"QC 评级闸门"改为"评级上报，不做强制" |
| `README.txt` | 第四/五/七节同步（不再声称闸门"写死在规则层、不是可选项"） |

验证结果：`qc.py` 全量重跑 40 格 → `pass=40 fail=0`，`rating_gate` 条目 0 条；
原先被闸门判失败的 `p008f`（`rating=sensitive` 0.912）现在 `status=pass, reasons=[]`，
评级仍照常记录。11 个 pipeline 模块导入自检 `IMPORT-OK`。

**受此影响、下文已失效或已改写的结论**：§2.3 中 `qc.py` 第 3 条与 `style_train.py` 的 prep 描述、
§四.C 第 31 条、§五 表格第 7 行、§六 总结中的"评级闸门"。其余结论不变。

**v2.2 —— 按官方调研结论升级模型选型与参数（依据见 `MODEL_SELECTION_2026.md`）**

| 文件 | 改动 |
|------|------|
| `pipeline/runner.py` | 按档位自动选型：正式档优先 `anima-aesthetic-v1.1`、草稿优先 `anima-turbo-v1.1`，缺失则回退 `anima-base-v1.0`（+turbo LoRA）；采样器默认 `euler+simple` → **`er_sde+sgm_uniform`**；正式档默认步数 30 → **40**；Aesthetic 下自动改用不含 `score_*` 的正/负向前缀（官方建议） |
| `pipeline/templates/anima_t2i.json` | 同步默认值（er_sde / sgm_uniform / 40 步） |
| `pipeline/agent.py` | 接入 **JSON Schema 约束解码**（`response_format`，服务端拒绝时自动降级回普通提示）；plan 用 PANELS/PAGES schema、judge 用 JUDGE schema；plan 与 finalize 步数 30 → 40 |
| `pipeline/style_train.py` | 默认 lr **1e-4 → 2e-5**、步数 **600 → 1200**；删除无效的 `--discrete_flow_shift`；新增 `--qwen_image_vae_2d`、移除已成 no-op 的 `--vae_disable_cache` |
| `pipeline/orchestrate.py` | `--train-steps` 默认 600 → 1200 |
| `start_agent.bat`、`tools/start_agent_headless.ps1` | 加 `--reasoning-budget 0` |
| `README.txt` | 档位表、训练参数、ctx/思考预算说明同步 |

**v2.18 —— 为什么 40 页漫画一点强度都没有：三个各自独立的缺口**

用户问"为什么内容这么平"。查完发现**没有任何东西在拦截它**——闸门在 v2.1 就拆干净了
（`qc.py` 的评级只记录不执行、正向词中性、负向词只压质量与伪字、judge 还被明确告知
`never flag a panel for its subject matter or rating`）。真正的问题是**三个互不相干的缺口**，
每一个单独都能让成品变成无害版。

**缺口一：剧情阶段从来没人要求过强度**

`STORY_SYSTEM` 里只有一句许可——`the premise sets the subject matter; there is no topic
restriction here`——但**没有任何一句是要求**。加上 `temperature=0.5`（偏低，模型收敛到最保险的
众数答案）以及"每句必须是镜头能拍到的：地点、动作、**情绪**"，结果 9B 把一个冲突激烈的前提写成了
纯情文艺片：40 个 beat 全在写氛围，唯一决定性事件挤在 30–32 句，然后**从"他领她走向卧室"
直接跳到"她回头满眼背叛的泪水"**——关键时刻整个被跳切掉。

修法：新增 `--adult`（`agent.py story` / `plan` + `orchestrate.py` 三级透传），把
`ADULT_DIRECTIVE` 追加进 system prompt，并把 temperature 提到 0.85。指令**点名它实际犯的两个
错误**，而不是泛泛要求"写得更强"：

- 不得用情绪、比喻或省略号替代事件（"写感受的句子不是写发生了什么的句子"）
- 不得在关键时刻跳切、不得直接跳到事后

实测 A/B（同一份前提，40 beat）：身体/动作 beat **12(30%) → 30(64%)**，情绪 beat **6(15%) → 1(2%)**。

**缺口二：`PAGE_SCHEMA` 里没有 `dialogue` 字段（这条最隐蔽）**

page mode 是**唯一**会做台词排版的模式，而它的 schema 少了 `dialogue`：

```python
PAGE_SCHEMA = {"properties": {id, panel_count, shot, scene, pose_action, tags},
               "required":   [...同上...], "additionalProperties": False}
```

`additionalProperties: False` 意味着**约束解码从结构上禁止**模型输出这个键。
`PANEL_SCHEMA`（非 page mode）有 `dialogue`，page mode 漏了。所以：

```
dialogue = (p.get("dialogue") or "").strip()[:40]   # 永远是 ""
```

连带后果是 `page_layout_tags(has_dialogue=False)` **不给页面加 `speech bubble` 标签**，
于是连空白气泡都不画——事后再想补字也没有地方写。40 页全空、0 个气泡。

**这条我改了三次提示词都没用**（先改 `SYSTEM_PAGE` 的措辞、再改用户消息里的 JSON 示例、
再把"beat 不引用台词、要自己编"写进去），因为**问题从来不在提示词上**。教训：约束解码下，
schema 才是硬约束；提示词改不动的东西，先去看 schema。

**缺口三：`comfy_yield()` 睡 2 秒就走，agent 静默掉回 CPU**

`/free` 是异步的。实测固定睡 2 秒后显卡只回到 1.5 GiB 空闲，而启动器**只采样一次**空闲显存
（阈值 6500 MiB），于是选了 CPU 构建——**整轮 LLM 阶段慢 9 倍，日志里只留一行**。

修法：改成轮询到启动器将要看到的那个数字（`AGENT_NEEDS_MIB = 6500`，与
`start_agent_headless.ps1` 对齐），并在日志里直说 `enough` / `NOT enough, agent will run on CPU`。

| 文件 | 改动 |
|------|------|
| `pipeline/agent.py` | 新增 `ADULT_DIRECTIVE`；`story`/`plan` 新增 `--adult`（temperature 0.5→0.85）；**`PAGE_SCHEMA` 补上 `dialogue`**；page mode 的台词规则改为"beat 不引用台词，你自己编" |
| `pipeline/orchestrate.py` | 新增 `--adult` 并透传给 story/plan；`comfy_yield()` 轮询真实显存替代固定 2 秒 |

顺带得到的两个副产品：加了 `--adult` 之后 planner 的标签真的变直白了
（`hands unbuttoning shirt` / `kneading bed, hips thrusting forward` / `penetrating thrust`），
而 `dialogue` 一进 schema，`speech bubble` 标签自动出现，排版链才第一次真正闭合。

**v2.17 —— `style_registry()` 少一个 `global`：40 页运行死在最后一步，以及那个测不出它的测试**

**现象**：40 页运行一路绿灯——40 张草稿全过、QC 64 张全 PASS、复核 0 重排、finalize 排好 40 张正式档——
然后**在 `render finals` 的第一张就崩**：

```
File "pipeline\runner.py", line 186, in style_registry
    if _STYLE_CACHE[0] != mtime:
UnboundLocalError: cannot access local variable '_STYLE_CACHE' where it is not associated with a value
```

**根因**：v2.14 新加的 `style_registry()` 里写了 `_STYLE_CACHE = (mtime, ...)`，Python 因此把
`_STYLE_CACHE` 判定为**整个函数的局部变量**，于是同一函数里前面那句读取直接抛 `UnboundLocalError`。
修法是加一行 `global _STYLE_CACHE`。

**为什么能潜伏这么久**：草稿走的是 turbo 分支，那个分支**根本不调用 `pick_unet(..., job)`**
（`build_workflow` 里 `pick_unet` 只在 `else`/正式分支被调用）。所以 `adapter_family` →
`style_registry` 这条链**只有正式档会走到**。40 张草稿、QC、复核、finalize 全部安全通过，
bug 精确地在最后一步引爆。

**为什么测试没抓到（真正该记的一条）**：`pipeline\test_routing.py` 里写了

```python
runner.style_registry = lambda: FAKE      # ← 把被测函数整个替换掉了
```

它测的是"如果注册表返回这些数据，路由对不对"，而**真实实现一次都没执行**。所以 8 个路由用例
全绿，却漏掉了一个函数体内的语法级错误。**一个把自己被测对象 stub 掉的测试，抓不到被测对象本身的问题。**

**修复**：

| 文件 | 改动 |
|------|------|
| `pipeline/runner.py` | `style_registry()` 加 `global _STYLE_CACHE`，并把"为什么能潜伏到正式档才炸"写进注释 |
| `pipeline/test_routing.py` | 重写：不再替换函数，改为把 `runner.STYLE_REG` 指向一个**真实的临时 styles.json**，让生产代码路径真正跑起来；新增**缓存失效断言**（改写文件后必须看到新 base）；把 `pick_unet` 与 `build_workflow` 的预期**分开断言** |
| `_stage/resume_40pages.py`（新） | 从 `render finals` 续跑：调用 orchestrate 自己的 `stage()` / `publish_pages()`，所以 GPU 交接与拼版行为跟正常运行一致，不重新规划（重新规划会丢掉已完成的 40 张草稿） |

最后一条顺带纠正了测试里另一个错：`ref_images` 那两行原本被判为 MISMATCH，其实**两边都对**——
`pick_unet` 从候选表里解析出 aesthetic，`build_workflow` 随后把它钉回 base，因为 in-context LoRA
是训在 base 上的。两个函数本就该给出不同答案，只断言其中一个就会把"正确"误报成"失败"。

**v2.16 —— 2.9B 训练在这台机器上跑得动：DataLoader worker 与分辨率**

v2.14 打通了"能在 2.9B 上训 LoRA"，但第一次真跑时 **45 秒/步、ETA 15 小时**——同一份日志里
2B 的稳态是 **1.6-2.0 秒/步**，也就是说慢了 **26 倍**，而 40 层相对 28 层只该慢 **1.43 倍**。
多出来的 25 倍全是内存停顿。

**诊断**：`--max_data_loader_n_workers` 的默认值是 **8**，而 `--cache_latents` 把 latent 缓存在
dataset 对象里——**每个 worker 各持一份**。实测 8 个 worker 各约 1GB，加主进程、ComfyUI、系统，
15.8GB 内存只剩 **0.1GiB**；显存同时被顶到 7700/8151（余 170MiB）。两边同时见底，驱动无处换页，
于是每步都在等。讽刺的是这个参数的官方帮助原文就写着 *"lower is less main RAM usage"*。

**三级实测**（118 张 / dim32 / 1200 步 / 同一张卡）：

| 配置 | 步速 | ETA | 空闲内存 |
|---|---|---|---|
| 768px + 8 workers（默认） | 43-45 s/it | ~15 小时 | 0.1 GiB |
| 768px + **0 workers** | 22.3 s/it | 7 小时 20 分 | 4.1 GiB |
| **512px + 0 workers** | **8.09 s/it** | **2 小时 38 分** | 4.4 GiB |
| （对照）2B 768px + 8 workers | 1.7 s/it | 34 分钟 | 充裕 |

512 再快 2.75 倍，是因为激活随 token 数走：768² 是 2304 token，512² 只有 1024。

**注意没有 fp8 退路**：`anima_train_network.py` 里 `--fp8_scaled` 那行是**被注释掉的**，
所以不能靠 fp8 把 5.44GiB 权重减半；`--blocks_to_swap` 理论上可用，但它要求先把整个 DiT
加载到 CPU 内存（5.44GiB），而当时只剩 4.4GiB，反而更危险。所以显存唯一的杠杆是分辨率。

| 文件 | 改动 |
|------|------|
| `pipeline/style_train.py` | 新增 `--workers`（默认 **0**），透传 `--max_data_loader_n_workers`；训练日志改为按 **run 目录**命名（`train_<name>_29b.log`），不再让 2B 与 2.9B 追加到同一个文件 |

最后一条是踩过的坑本身：2B 与 2.9B 共用一个 append-only 日志，正是上一轮让我"读着旧运行的数据
得出关于当前运行的结论"的那种陷阱；run 目录已经区分了架构，日志也该跟着区分。

**v2.15 —— Agent 迁到 GPU：CUDA 构建上生产、启动器自动选型、orchestrate 阶段交接**

**起因**：一直守着"CPU 留 0 显存给生图"，代价是解码 4.52 tok/s——实测瓶颈是**单通道内存带宽**
（4.52 × 5.29GiB = 25.8GB/s ≈ 单通道 41.6GB/s 的 62%），换线程/优先级/ZenDNN 都无效。
而流水线的阶段是**串行**的：plan/judge 不渲染，render 不用 LLM，所以显存可以分时复用。

**实测更正（推翻自己 9/26 的结论）**：

| 配置 | decode | 视觉 judge |
|---|---|---|
| CPU `-ngl 0` | 4.52 tok/s | 在位，占 0 显存 |
| GPU `-ngl 28` + mmproj（旧结论） | 21.23 | 在位 |
| **GPU `-ngl 99` + mmproj（本次实测）** | **41.86** | **在位，并没有被挤掉** |

旧结论说"全量卸载会挤掉视觉"，真正的原因是**当时没有先把 ComfyUI 完全交出来**。整卡 8151MiB，
ComfyUI `/free` 之后可用 6608MiB，9B + 视觉投影加载完占 7605MiB（余 287MiB）——挤得进，
而且比 `-ngl 28` 快一倍。余量只有 287MiB，这恰恰反过来说明**阶段交接是必需的**。

**第二个发现**：CUDA 构建**即使 `-ngl 0` 也占约 1.1GB 显存**（CUDA 上下文 + 计算缓冲，
实测 1028→2384MiB）。所以"回退到 CPU"必须**换二进制**，不能只把层数调成 0——那样白占 1.1GB
还一点不快，2.9B 出图（峰值约 7.0GB）直接放不下。

| 文件 | 改动 |
|------|------|
| `tools/llamacpp/` | CPU-only 构建 → **CUDA 13.4 build 11195**（原地替换；`cublasLt64_13.dll` 470MB、`ggml-cuda.dll` 140MB、`cudart64_13.dll`）。文件清单是旧构建的**严格超集**，无缺件、无零字节文件 |
| `tools/llamacpp-cpu/` | 旧 CPU 构建（build 11177）**留作备份**，同时是真正的 0 显存回退路径 |
| `tools/start_agent_headless.ps1` | 新增 `-Ngl auto`（默认）：nvidia-smi 读空闲显存，≥6500MiB 走 CUDA `-ngl 99`，否则走 CPU 构建 `-ngl 0`；启动后**验活**（轮询 `/v1/models`），GPU 起不来自动回退 CPU 一次；决定同时写进 `workspace\logs\agent-launch.log` |
| `tools/stop_agent.ps1`（新） | 按可执行文件路径**只停本站** `tools\llamacpp*` 起的服务，别处的 llama-server 不碰 |
| `start_agent.bat` | 改为委托 `start_agent_headless.ps1`，不再硬编码 `-ngl 0`（保持纯 ASCII） |
| `pipeline/orchestrate.py` | `stage(..., needs="llm"\|"gpu")`：llm 阶段先 `/free` ComfyUI 再起 agent，gpu 阶段停掉 agent。新增 `comfy_yield()` / `agent_release()` / `agent_mode()`；启动时**不再抢跑 agent**——否则它会拿 ComfyUI 剩下的显存启动，整轮悄悄退回 CPU |

**一个坑**：启动器**不能被抓取输出**。它拉起的服务继承了句柄，`subprocess.run(capture_output=True)`
会一直阻塞到该服务退出（实测卡死两次，且超时后的收尾 `communicate()` 也会卡住）；
`Popen` 不 capture 则 0.01 秒返回。所以决定写日志、运行日志再读回来。

**为什么是停/启而不是卸载**：llama.cpp 服务端没有卸载接口——`/slots/0?action=sleep` 返回 501，
`/sleep`、`/wake`、`/models/unload`、`/unload`、`/models/sleep` 全部 404；只有
`--sleep-idle-seconds` 的"按空闲自动休眠"，触发时机不可控，不能用于阶段边界。

**实测一次完整交接**（`pipeline/test_handoff.py`）：ComfyUI 持模型时 1257MiB 可用 → `/free` 后
6919MiB → agent 冷启动 14 秒（`ngl=99`、视觉在位）→ 停止 2.8 秒 → 6919MiB 全部还给渲染 →
**ComfyUI 自己重新加载模型并成功出 2.9B 图（67.3 秒），不需要重启 ComfyUI**。第二轮同样通过。
一轮约 5-6 次交接 ≈ 1.5-2 分钟，换来 LLM 快 9.3 倍。

**v2.14 —— 2.9B 画质解锁：选型按适配器路由，LoRA 能训在 40 层上**

**问题**：Anima-2.9B 是 28 层 base 的 **40 层扩层版**（manifest 插入点 2,5,8,…,36），画质更好，
但两个障碍让它"能出图、不能用"：

1. 已有 5 个适配器**全部只寻址 blocks 0-27**。这些块在 40 层模型上仍然存在，所以 LoRA
   **加载不报错、落在错层上**——静默劣化，不是崩溃。
2. kohya **根本训不了**：`library/anima_utils.py` 写死 `num_blocks: 28`，加载 2.9B 会在
   `blocks.28…` 上抛 `Unexpected keys in checkpoint`。

| 文件 | 改动 |
|------|------|
| `pipeline/runner.py` | `pick_unet` 从"有适配器就回 2B"改成**按适配器路由**：`style_lora` 看它在 `styles.json` 里记录的 `base`；`ref_images`/`pose_image` 是第三方 28 层 LoRA、我们重训不了，**永久钉住 2B**；完全无适配器才用 2.9B。新增 `style_registry()`（带 mtime 缓存，长跑时能吃到并发 deploy 的新记录）与 `adapter_family()`。草稿档不变（仍 turbo 2B），"全部替换成 2.9B"会让草稿从 7-10 秒涨到 60-90 秒 |
| `pipeline/style_train.py` | 新增 `--base {base\|aesthetic\|2.9b}`（也接受完整文件名）。`resolve_base()` / `run_dir()`：**不同架构的 checkpoint 绝不共目录**——probe 是 glob `*.safetensors`、deploy 是拷赢家，2B 旧档混进来会在错底模上评测、甚至被部署。2.9B 走 `trainer/out/<name>_29b/`，2B 历史路径不动。deploy 记录 `base` 字段，被顶替的适配器移到 `models/loras/_superseded/` 而**不是删除**（它是唯一还配另一种架构的那份） |
| `trainer/sd-scripts/library/anima_utils.py` | **本地补丁**：`num_blocks` 改为从 checkpoint 的 safetensors 头**推断**（不加载权重、只数顶层 `blocks.N`，刻意排除 `llm_adapter.blocks`，否则数目会被带偏）。实测两个架构**只有深度之差**：2B = 685 张量/28 层/2048 宽，2.9B = 928/40/2048。28 层仍得 28，旧流程逐字节不变 |
| `pipeline/test_routing.py`（新） | 8 种适配器组合的路由回归测试 |

**验证**：静态加载校验四个 checkpoint 全部干净——2.9B 928/928、0 unexpected、0 missing；
2B 三个各有 3 个官方未保存的 buffer，加载器白名单本就认（`seq`/`inv_freq` 等）。
真实出图 69.0 秒、峰值 7.03/7.96 GiB、画面解剖正确（对照之前 2B hires 测试里糊掉的手，这张手是好的）。

**v2.13 —— 画师风格索引建成，画师自动化打通**

**问题**：画师表 1200 条只有"名字 + 帖数"。帖数只回答"Anima 认不认得这个名字"，**完全没说它长什么样**，
所以自动选画师没有任何可推理的材料。网上也没有现成数据集：Anima Style Gallery 是无接口的 JS 应用，
kit 里两张画师表都只有名字和计数（已查证）。

**出路**：kit 的数据文件里**每条都带官方示例图直链**。于是本地自建索引 ——
`pipeline/build_artist_styles.py` 下载示例图、交给**已经挂在 llama-server 上的视觉模型**（`--mmproj`）
只描述画风，存成关键词。图片先降到 512（与 judge 同一条实测结论：图片 token 主导预填成本）。

| 文件 | 改动 |
|------|------|
| `pipeline/build_artist_styles.py`（新） | 可续跑：示例图落盘缓存、已完成的画师跳过、每位完成后立即落盘并打印 |
| `pipeline/presets/artist_styles.json`（新） | 100 位画师的风格关键词（约 10 秒/位，共 ~17 分钟） |
| `pipeline/presets.py` | 新增 `artist_styles()` / `artist_menu(limit)` |
| `pipeline/agent.py` | 新增 `choose_artists()` + `--auto-artist N`（与预设同一套"验证式挑选"） |
| `pipeline/orchestrate.py` | 新增 `--auto-artist N` 透传 |

**关于索引质量的一次自我更正**：跑的过程中我担心描述高度重复（「赛璐璐」出现率 50%、
「高饱和度」47%），认为索引可能没有区分度。**量化后这个担忧大部分被推翻**：

| 指标 | 值 |
|---|---|
| 不同关键词数 | 109 / 100 位 |
| **完全相同的关键词组合** | **仅 1 组重复（99/100 各不相同）** |

**词汇平庸 ≠ 描述无区分度**——组合几乎全不同。检索也精确命中：
`赛博朋克 霓虹 高对比` → 正好检出那三位描述含"赛博朋克"的画师；`水彩 清新 明亮` → `yaegashi nan`（清新水彩）。

**真实端到端测试（两次均 rc=0）**：

| 故事 | 自动选出 | 索引描述 |
|---|---|---|
| 赛博朋克夜城 | `@hammer (sunset beach)`、`@neocoill` | 赛博朋克，霓虹色调 / 动漫风格，赛博朋克 |
| 温柔水彩日常 | `@yaegashi nan`、`@kagami hirotaka` | **清新水彩** / 清新明亮 |

**留存的局限**：高频词（赛璐璐/高饱和）不携带信息，真正起作用的是少数**有辨识度的词**
（赛博朋克、透明质感、无渐变、半色调网点、硬阴影）。所以索引的区分力集中在长尾——
若哪天要把索引扩到 400 位，这个问题会放大，届时应改成"相对其他画师，这位的独特之处"式提问。

---

**v2.12 —— judge 闭环：复核裁决可以触发有界重出**

此前 judge 是 **dry by design** —— 裁决只写 `audit.jsonl`，从不排任务。这是"全自动 + 高质量"最后一块
结构性缺失：复核发现了问题却什么也不做。

现在 `--reroll` 让 `reroll` 裁决排一张**同意图、新种子、正式档画质**的替换图，
**两重上限**保证坏 judge 不会变成无限出图：

| 边界 | 机制 |
|---|---|
| 每页只给一次第二次机会 | 新任务带 `judge_reroll` 标记；`beat_key()` 把 `p008`/`p008f`/`p008r1f` 归为同一页，已花掉机会的页直接跳过 |
| 每次运行总额封顶 | `--reroll-max`（默认 4） |

| 文件 | 改动 |
|------|------|
| `pipeline/agent.py` | 新增 `beat_key()`；`cmd_judge` 加 `--reroll` / `--reroll-max`；不带 `--reroll` 时保持 dry 并打印"有 N 条裁决未被执行" |
| `pipeline/orchestrate.py` | 加 `--judge-reroll`（**默认开**）/ `--judge-reroll-max`；judge 之后补一次 `runner.py` 渲染（无待办时是空操作）；`publish_pages` 按 `beat_key` 去重 |
| `README.txt` | 待同步 |

**命名与去重是关键集成点**：重出图 id 为 `<页>r1f`（仍以 `f` 结尾，所以 publish 的"只认正式档"
筛选不会漏掉它），但这样一来 `p008f` 与 `p008r1f` 会各自发布一次，所以 `publish_pages` 必须按页去重，
**靠后的赢**（重出图总是排在它所替换的正式档之后）。

`beat_key()` 在 `agent.py` 与 `orchestrate.py` 各有一份 —— orchestrate 是以子进程方式驱动 agent.py
而非 import，为避免耦合，这个纯函数选择重复实现，两边一致性已单测。

**打桩验证（未烧 GPU 时间）**：

| 场景 | 结果 |
|---|---|
| 两页都判 reroll、预算 1 | 只排 1 张（`p001r1f`），种子 100+7919=8019，带 `judge_reroll` 标记 ✓ |
| 紧接着再跑一次 | 给 **p002** 它的首次第二次机会，**不再动 p001** ✓ 深度封顶 |
| 不带 `--reroll` | 不排任何任务，日志提示"2 reroll verdict(s) not acted on" ✓ 仍是 dry |
| `publish_pages` 去重 | 保留 `p001r1f` + `p002f`，重出图胜出且不重复 ✓ |

---

**v2.11 —— 视觉复核的图片分辨率降到 512（judge 提速 2.4×，实测标定）**

起因是一次**自我更正**：v2.9 之后我把 Jev 决策模型评为"杠杆最大"，理由是 judge 每格要 66 秒。
实测后发现 **66 秒是我错误外推的**（从"300 token 基准"推一个只生成 30-40 token 的负载）：

| judge 路径 | 预填 | 生成 | 合计 |
|---|---|---|---|
| 纯文本 | 450 tok @ 99.2 tok/s = 4.5 s | 40 tok @ 5.14 = 7.6 s | 12.3 s |
| **带视觉（生产默认）** | 804 tok @ 45.6 tok/s = **17.6 s** | 30 tok @ 5.15 = 5.6 s | **23.4 s** |

**生产路径 75% 的时间是预填，而其中 354 token 来自图片。** Jev 是文本分类器、读不了图，
所以它只能优化那 5.6 秒生成、且只在非默认的纯文本路径上 —— **评级从"最高杠杆"下调为"路径特定的小优化"**。

真正的杠杆是图片分辨率。实测同一张 1024² 面板（提示词 token / 总耗时）：

| max_side | 提示词 token | 预填秒 | 预填 tok/s | 总耗时 |
|---|---|---|---|---|
| 768（原值） | 836 | 14.7 | 41.5 | 16.2 s |
| 640 | 660 | 9.0 | 48.3 | 10.5 s |
| **512（新默认）** | **516** | **5.3** | **55.1** | **6.7 s** |
| 384 | 404 | 3.0 | 59.7 | 4.5 s |
| 256 | 324 | 1.7 | 59.8 | 3.0 s |

**图片 token 数随分辨率缩放**，且预填吞吐本身也提升。取 512 作为拐点：判缺陷（手崩、多肢、伪字、
裁脸）所需细节在此尺寸下保留，再降有风险。

顺带确定了后续可做的事：judge 现在 6.7 s/格，**全量 40 格约 4.5 分钟**，`--judge-limit 6` 的限流
已无必要。

---

**v2.10 —— InContext 角色参考节点包安装并实测通过（跨页角色一致性解锁）**

v2.9 里发现的回归是"LoRA 到位但节点缺失，`--ref-img` 会渲染失败"。节点包的源码其实**就在同一个
HF 仓库里**（`darask0/Anima-InContext-Character` 下的 `comfyui-anima-incontext/`），不需要 GitHub：

| 文件 | 说明 |
|------|------|
| `nodes.py` | `AnimaRefEncode` / `AnimaRefLatentBatch` / `AnimaInContextApply` |
| `incontext.py` | 核心实现（`apply_incontext_ref`、`_fit_latent`） |
| `style_nodes.py` / `style_adapter.py` | 附带 3 个风格适配器节点（需 `clip_vision` + 适配器文件，暂未使用） |
| `workflow_anima_incontext_character.json` | 官方工作流，留作契约参考 |

安装到 `ComfyUI\custom_nodes\comfyui-anima-incontext\`，重启 headless ComfyUI 后 8 个节点全部识别。

**输入契约已逐项核对**（这一步不能省，ONNX 那次的 `orig_target_sizes` 就是这么漏的）：
`AnimaRefEncode` 必填 `vae`/`image`，可选 `mask`/`target_width`/`target_height`；
`AnimaInContextApply` 必填 `model`/`ref_latent`/`strength`/`start_percent`/`end_percent`，
可选 `cond_only`/`fit_mode`/`ref_timestep` —— **runner 构建的参数与作者契约完全一致**。

**实测（同种子 31337，提示词不含任何角色描述）**：

| | 耗时 | 结果 |
|---|---|---|
| 无参考（对照） | 50.8 s | success |
| **带 InContext 参考** | **159.8 s** | success，图里确有 `AnimaInContextApply` |

**看图验证**：参考图是金发长发侧脸少女（青绿瞳），输出是**同一头金发长发**（瞳色漂为琥珀金）。
身份的主特征转移成功，**瞳色有偏移**。代价是**渲染时间 3×**（参考帧增加了序列长度）。

**作者文档里一条尚未利用的质量提示**：`AnimaRefEncode` 可接 `mask` 把主体合成到白底，
**"对动漫角色，去背景能明显提升外观保真度"**。runner 目前不传 mask（本地没有分割模型），
这是下一步可提升点。

---

**v2.9 —— 接入社区提示词工具包的数据（预设库）+ 修掉由此暴露的两个缺陷**

用户提供的 5 个下载文件里，有价值的是**数据**而不是节点。`build_presets.py`（可复现脚本）
把 AnimaPromptKit 的数据文件与用户自己的服饰标签表转成 `pipeline/presets/presets.json`：
**466 条预设 / 400 位画师（按训练语料帖数排序）/ 433 条画师配方 / 8 个负向包 / 14 组成人标签**。

| 文件 | 改动 |
|------|------|
| `pipeline/build_presets.py`（新） | 转换脚本，把出处与"被过滤内容"一并写进产物；**只对成人向预设**做幼态性化过滤（非成人预设里出现婴儿不误杀） |
| `pipeline/presets.py`（新） | 预设库读取 + 发现用 CLI（`--list / --artists / --negatives / --groups / --show`） |
| `pipeline/agent.py` | `plan` 新增 `--artist`（可重复）/ `--preset`（可重复）/ `--negative-pack`；`@画师` 插在 trigger 之后（符合模型卡标签顺序）、预设标签并入词表、预设的自然语言句接在词表末尾；负向包替换默认值但**仍追加反伪影块、并按台词有无分流气泡** |

**两个由此暴露并被修掉的真缺陷**：

1. **JSON Schema 约束解码并不保证"整段可解析"** —— v2.2 里那句注释说过头了。grammar 只保证
   到**根对象闭合**为止，之后模型可以继续写。实测 plan **3/3 次在同一位置失败**
   （`Expecting ',' delimiter: char 183`），每次耗时 **6.7 分钟**（写满 1800 token）。
   修法两条：① 给 schema 数组加 **`maxItems`**（服务端支持，同时止住跑飞——规划从
   "6.7 分钟 ×3 失败"变成 **97 秒成功**）；② 把 `parse_json` 的兜底从**贪婪** `\{.*\}`
   换成**首个花括号平衡**的提取（贪婪版会把"合法对象 + 尾随垃圾"整段圈进去，同样失败）。
   另外 `llm_json` 失败时现在会打印原始回复的首尾，便于定位。
2. **`--negative-pack` 会绕过 runner 的气泡规则**：job 级 `negative` 被 runner 原样使用，
   于是留白页不再禁气泡。现在 agent 侧按 `allow_bubble` 补 `NEG_BUBBLE`，与默认路径一致。

**"用这些文件训练"不可行**：5 个文件里**没有任何图片**，无法直接用于训练。它们能带来的
间接收益是让 `make_style_dataset.py` 生成内容更多样的自有素材（画风 LoRA 更不容易把某个
场景或服装学进画风），本次**未改训练链路**。

---

**v2.8 —— 两段式精修（hires）接入 runner**

依据是社区工作流 `anima文生图（lora+controlnet+2k）` 的拓扑：768² 一段 → `LatentUpscaleBy`
（nearest-exact ×2）→ 二段 KSampler（denoise 0.37、步数与 CFG 同一段）→ 从二段 `VAEDecode`。
**该工作流的二段绕过了 `AnimaControlApply` 与 `CFGZeroStar`；本实现有意不照搬**——二段保留
完整适配器链，否则 InContext 角色参考会在"加细节的那一段"里丢掉身份。

| 文件 | 改动 |
|------|------|
| `pipeline/runner.py` | 读 `hires` / `hires_denoise` / `hires_method`：新增节点 90 `LatentUpscaleBy` 与 91 二段 `KSampler`，并把 `VAEDecode` 改读 91；超出 `HIRES_MP_WARN=2.4MP` 时打 WARNING 并继续；dry-run 行增加 `hires=` |
| `pipeline/agent.py` | `plan` / `finalize` 新增 `--hires` / `--hires-denoise`；finalize 仅在该参数显式给出时覆盖草稿上已有的值 |
| `pipeline/orchestrate.py` | 新增 `--hires` / `--hires-denoise`，透传给 finalize（**只作用于正式档**） |
| `README.txt` | 档位表加入两段式一行，记录实测耗时/显存与 denoise 对比 |

**实测（本机 8GB，同种子 4242，1024² 基准，全部 `status=success`，未 OOM）**：

| 配置 | 耗时 | 产出 | 峰值显存 | 观察 |
|---|---|---|---|---|
| 单段 40 步 | 49.2 s | 1024×1024 | ≈6.6 GB | 干净完整 |
| hires 1.5 / denoise 0.40 | 125.5 s | 1536×1536 | ≈5.6 GB | 细节最多，但**手指与领口被重画糊** |
| hires 1.5 / denoise 0.37 | 121.9 s | 1536×1536 | — | 同区间 |
| **hires 1.5 / denoise 0.25** | 165.9 s | 1536×1536 | — | **手部干净、细节仍明显高于单段 → 选为默认** |

耗时区间 120-170 秒的波动来自 ComfyUI 在紧显存下的模型重载，属正常。
对照样本留在 `workspace\out\raw\hirestest_*.png`。

---

**v2.7 —— 默认改为「一张图 = 一页」，对白按需写入**

原先 page mode 是可选且**明确无字**（负向词禁 `speech bubble`，规划器把 dialogue 置空）。
现在它是默认；而对白**只在需要时**才有——规划器按剧情决定：有人说话的页给一句台词，
留白页 / 纯动作页 / 氛围页给空字符串。

原理：扩散模型画不对字，但**画得对气泡形状**。所以拆成两半——有台词的页由**代码**
（不交给模型）插入 `speech bubble` 标签、并只对该任务放行负向词里的气泡，模型画出一个
空泡；文字检测器找到它，Pillow 涂白并把真中文写进去；找不到气泡时在页面下三分之一
兜底画一个。没有台词的页照旧禁气泡、也不加字，**不会留下无人可填的空泡**。

| 文件 | 改动 |
|------|------|
| `pipeline/compose.py` | 新增 `overlay_dialogue()` 与 `--page/--dialogue` 入口：优先写入检测到的最大气泡（涂白可顺带擦掉模型画的伪字），无气泡则兜底；字号按气泡尺寸自动缩到装得下 |
| `pipeline/text_detect.py` | 拆出 `detect(pil_image)`，让 compose 能直接吃内存里的页面，不必落盘再读 |
| `pipeline/agent.py` | `SYSTEM_PAGE` 改为「每页留一个**空白**气泡 + 必须给台词」；`page_layout_tags()` 加入 `speech bubble` 标签（确定性起见不交给模型）；page mode 的 dialogue 不再置空；新增 `allow_bubble` 任务字段 |
| `pipeline/runner.py` | `negative_default(unet, allow_bubble)`：仅对声明 `allow_bubble` 的任务放行 `speech bubble`，其余任务照旧禁气泡 |
| `pipeline/orchestrate.py` | `--page-mode` 默认 **True**（`--no-page-mode` 回到旧的拼版流程）；`publish_pages()` 逐页调 compose 写字，写字失败则退回纯拷贝 |
| `pipeline/agent.py` CLI | `plan --page-mode` 同样默认 True，保持手动分段流程一致 |

**实测验证**（含看图确认）：

| 场景 | 结果 |
|---|---|
| 含真气泡的页面 | 文字准确写入检测到的气泡、涂白、CJK 正确折行、清晰可读 |
| page mode 无气泡单页 | 兜底气泡（圆角框 + 尾巴）落在下三分之一，两行中文排版正常 —— **即"一张图 = 一页且有字"的目标形态** |
| 负向词分流 | 普通任务 `neg` 仍含 `speech bubble`；`allow_bubble` 任务不含，且正向词已带该标签 |

**保留样例**：`workspace\out\pages\sample_lettered_page.png`

---

**v2.6 —— 第二批：角色参考一致性 + 姿态控制 + 漫画文字检测**

| 文件 | 改动 |
|------|------|
| `pipeline/text_detect.py`（新） | RT-DETR-v2 漫画文字/气泡检测器，CPU onnxruntime，零显存。三类：`bubble` / `text_bubble` / `text_free` |
| `pipeline/qc.py` | 第 5 项检查接入检测器：`text_counts` / `text_boxes` 写进 qc.json，`text=N` 打进 PASS/FAIL 行；`text_detected:N` 默认**建议性**，加 `--text-gate` 才变硬失败；模型缺失时只关掉这一项检查，不影响整轮 |
| `pipeline/runner.py` | `build_workflow` 重写为**模型适配器链**：base → 风格 LoRA → 角色参考（InContext）→ 姿态控制。新增 `ref_images`（最多 2 张，节点 70-76）与 `pose_image`（节点 80-83）任务字段；新增 `stage_input_image()` 把参考图拷进 ComfyUI 的 input 目录；**新增 `safetensors_complete()`**——只有整个张量块都在磁盘上才算可用（下载中的半截文件不再被选中），适配器权重缺失时告警并干净降级 |
| `pipeline/agent.py` | `plan` 新增 `--ref-img`（可重复）/ `--ref-strength`，写进每个任务 |
| `pipeline/orchestrate.py` | 新增 `--ref-img` / `--ref-strength`，透传给 plan |

**实测验证**：

| 项 | 结果 |
|---|---|
| 节点图结构 | 6 种组合（纯正式/风格/参考×2/风格+参考/姿态/全开）全部**无悬空引用**，模型链正确：`['1',0]` → `['10',0]` → `['76',0]` → `['83',0]` |
| 文字检测真值 | 成品页 4 个中文气泡**全部命中**（`bubble` 0.951，`text_bubble` 0.873 精确嵌套在气泡框内）；无文字的 `p001.png` **零误报** |
| 意外收获 | 草稿 `p003.png` 检出 1 处 `text_free`，其正式档 `p003f.png` 为 0 —— 正好印证 README 里"Turbo 档 CFG=1 负向词物理失效会带伪文字"那条 |
| 半截文件防护 | 下载中的 `anima-aesthetic-v1.1.safetensors`（190MB/4.18GB）判定为**不可用**，选型自动回退 base；参考图请求在 LoRA 未就绪时告警并降级为无参考 |

**模型输入签名**（踩坑记录）：该 ONNX 需要**两个**输入——`images` 与 `orig_target_sizes`；导出图自带 sigmoid 与坐标反算，输出 `labels`/`boxes`(xyxy 原图像素)/`scores`。

---

**v2.5 —— 无人值守链路全自动：plan 自动思考 + judge 自动进链路**

| 文件 | 改动 |
|------|------|
| `pipeline/orchestrate.py` | 新增 6 个开关（`--think-plan`、`--judge`、`--judge-vision`、`--judge-think`、`--judge-limit`、`--judge-think-below`），**默认全开**；plan 阶段自动带 `--think`；新增 **judge 阶段**（位置在正式档渲染之后、page mode 返回之前，所以两种模式都会跑）；启动时打印思考开销与前提提示 |
| `pipeline/agent.py` | `judge` 新增 `--think-below R`（默认 0.5）：**按格决定是否思考**——只有标签命中率低于 R 的格子才花思考预算；日志与 `audit.jsonl` 增加 `think` 字段 |

**仍然保持只读**：judge 的结论只进 `audit.jsonl`，不排队重跑、不改变成品页——沿用原作者的
"硬规则与模型决策分离"设计。

**集成验证**（打桩 `stage()` / `ensure_service()` 后实跑 `orchestrate.main()`）：

| 配置 | 实际发出的命令 |
|------|----------------|
| 默认（全自动） | `plan … --draft --think` ＋ `judge --limit 6 --think-below 0.5 --vision --think` |
| `--no-think-plan --no-judge` | `plan … --draft`（无 `--think`），且**完全没有 judge 阶段** |
| `--no-think-plan --judge-think-below 1.0` | judge 每格都思考 |

---

**v2.4 —— 可选思考模式（`--think`，默认关闭）**

Qwen3.5-9B 是否思考由 chat template 的 `enable_thinking` 决定（**默认关闭**：未指定时模板注入
空 think 块）。本机实测同一问题：关闭 **8.6 s / 20 token** 干净作答；打开 **66 s / 300 token** 且
`finish_reason=length`、`content` 为空——**只翻开关会让 `llm_json` 连续三次解析失败并抛错**。

| 文件 | 改动 |
|------|------|
| `pipeline/agent.py` | `chat()` / `llm_json()` 新增 `think`；新增 `THINK_BUDGET=1024` 与 `tokens_for()`（思考与答案共享 `max_tokens`，开启时自动加 1024）；新增 `server_ctx()` + `warn_if_ctx_tight()` 防超上下文；开启时把思考字符数/耗时写进日志。**修正**：只有错误信息确实提到 `response_format`/`json_schema`/`grammar` 时才关闭约束解码（原先任何 HTTPError 都会误关） |
| `pipeline/agent.py` CLI | `story` / `plan` / `judge` 新增 `--think`；`main()` 在开启时做一次容量检查 |
| `start_agent.bat`、`tools/start_agent_headless.ps1` | `--reasoning-budget 0` → **1024**（0 会让思考立即结束 = 静默禁用 `--think`；该参数仅在请求开启思考时生效） |
| `README.txt` | 记录 `--think` 用法、实测代价与建议适用范围 |

**默认行为逐字节不变**：不传 `--think` 即 `enable_thinking=false`。ctx 仍为 6144——
plan 的实测占用（提示词约 1200 + max_tokens 2824）在预算内，**无需加大 context、不增加内存压力**。

---

**v2.3 —— 删除 4B 大脑，只保留 9B**

| 文件 | 改动 |
|------|------|
| `tools/gguf/Qwen3-4B-Q4_K_M.gguf` | **已删除**（释放 2.33 GiB，全盘复查无残留副本） |
| `tools/fetch.ps1`、`tools/fetch_ms.ps1`、`tools/install.ps1` | 权重清单里的 `Qwen3-4B-Q4_K_M.gguf` → **`Qwen3.5-9B-Q4_K_M.gguf`**（hf-mirror 与 ModelScope 路径均按 `unsloth/Qwen3.5-9B-GGUF` 的实际文件名核实过） |
| `start_agent.bat` | 模型改指 9B；`-t 12 -c 16384` → **`-t 16 -c 6144`**，与 `start_agent_headless.ps1` 对齐（9B 的 KV cache 要和 ComfyUI 一起挤进 16GB 内存，上下文不能贪大） |
| `tools/start_agent_headless.ps1` | 删除注释里的 4B 回退示例 |
| `pipeline/agent.py` | `cmd_story` 中提及 4B 的注释改写（**代码逻辑未动**） |
| `README.txt` | 删除"回退 4B"的操作指引；ctx 4096 → **6144**（此前文档与代码不符） |

---

## 一、这是什么

一条**全本地、可离线、单文件夹自包含的动漫漫画（分格/条漫）生产流水线**，三条腿：

| 角色 | 组件 | 干什么 | 资源 |
|------|------|--------|------|
| 大脑 | llama.cpp llama-server + Qwen3.5-9B GGUF | 出剧情、拆分镜、写提示词、监管产出（judge） | **纯 CPU（`-ngl 0`），0 显存** |
| 画笔 | ComfyUI + Anima 2B DiT（Qwen3-0.6B TE + Qwen Image VAE） | 出分格图 / 整页图 | GPU，8GB 贴线（峰值 ~6.8GB） |
| 教练 | kohya sd-scripts（`anima_train_network.py`）+ WD-EVA02 ONNX 打标器 | 用自有素材训 LoRA，自动评测选点并部署 | GPU（训练时独占） |
| 排版 | Pillow | 拼版 + 中文对白气泡叠加 | CPU |

三个入口：`start_comfyui.bat`（GUI 出图，8188）、`batch_run.bat`（无人值守全链路）、
`run_style_train.bat` / `python pipeline\style_train.py run --name X`（训 LoRA）。

---

## 二、核心原理：文件契约驱动的状态机

没有数据库、没有消息队列、没有常驻编排框架。进程之间只靠 `workspace/jobs/` 下的文件传递状态，
每个进程只干一件事，干完就退出。**这是读懂全部代码的钥匙。**

### 2.1 契约文件

| 文件 | 角色 | 写者 | 读者 |
|------|------|------|------|
| `jobs.jsonl` | 任务队列，每行一格（prompt/尺寸/seed/对白/tags/LoRA） | `agent.py plan/review/finalize` | `runner.py`、`qc.py`、`compose.py` |
| `control.json` | 控制意图 `{"mode":"run\|pause\|stop"}` | `agent.py control` | `runner.py`（每格前读一次） |
| `state.json` | 断点续跑状态（`os.replace` 原子写） | `runner.py` | `runner.py`、`agent.py` |
| `qc.json` | 质检报告（技术校验 + 评级 + 标签命中） | `qc.py` | `agent.py review/judge` |
| `audit.jsonl` | 决策追溯（只追加） | `agent.py` | 人 |
| `styles.json` | LoRA 注册表（trigger/文件/强度/评分） | `style_train.py deploy` | `agent.py plan` |

注意：**`control.json` 当前根本不存在**，`read_json` 返回 `{}`，`mode` 默认 `"run"` —— 控制通道是
"备用阀门"，日常跑批并未使用它。

### 2.2 主数据流

```
story.txt
   │  agent.py plan  (split_beats 代码切段 → LLM 每 4 格一批出 JSON)
   ▼
jobs.jsonl ──► runner.py ──► ComfyUI:8188 ──► workspace/out/raw/*.png
   │              └─ state.json(done/failed, 原子写)      └─► out/panels/<id>.png
   │  agent.py review ◄── qc.json ◄── qc.py (CPU: 解码/空白/尺寸/dHash/评级闸门/标签命中)
   │        └─► 新 id(p00xrN) + 新 seed 写回 jobs.jsonl
   ▼  agent.py finalize (同 seed, 30步/CFG4, id 加 f 后缀)
runner.py ──► compose.py (cover 裁剪拼版 + 中文气泡) ──► out/pages/page_*.png
```

### 2.3 逐模块要点

**`comfy.py`（114 行）** 极简 ComfyUI 客户端，**只用 urllib**（要跑在 ComfyUI 自带
`python_embeded` 上，不能加依赖）。
- `submit()` 只保留带 `class_type` 的顶层键（L62-69）：ComfyUI 的 `validate_prompt` 会对每个顶层条目
  调 `.get()`，任何注释键都会导致 HTTP 500。
- `free()` 不解析 JSON（L52-60）：`/free` 返回空 body。
- `wait()` 轮询 `/history/<id>` 并采样最小空闲显存（L75-92）。

**`runner.py`（230 行）** 渲染执行器，三条写死在注释里的准则：只渲染（QC 单独一轮，CPU 打标器永不与
GPU 抢资源）、可续跑（state.json 原子替换 L64-69/L211）、可暂停（每格前读 control，L168-175）。
- `build_workflow()` 用**硬编码节点 ID** 改模板：1 UNET / 2 CLIPLoader / 3 VAE / 10 LoraLoaderModelOnly /
  20 正向 / 21 负向 / 30 EmptyLatent / 40 KSampler / 50 VAEDecode / 60 SaveImage（L90-123）。
- 三种模式：`turbo` → 挂 Turbo LoRA、10 步 / CFG 1.0、`KSampler.model=["10",0]`；`style_lora` → 30 步 / CFG 4.0；
  否则裸模型 30 步 / CFG 4.0。
- 正向自动补 `POS_PREFIX`（L38）；负向有一套**反伪影块**：`watermark, signature, logo, text, english text,
  japanese text, twitter username, web address, copyright name, dated, speech bubble`（L41-46）——
  booru 系模型爱自己画角标、伪字、签名。

**`agent.py`（767 行，核心）** 本地 LLM 的四种职责：出词（`plan`/`story`）、控制（`control`）、
监管（`review`/`judge`）、收尾（`finalize`）。
- 硬规则留在代码里：`split_beats()` 在代码里切 N 段防漏剧情点（L89-109）；`CHUNK=4` 保持 JSON 短小（L41）；
  `seed_for()` = sha256(镜头+场景+job id) 取前 12 位十六进制 → **同输入永远同 seed，可复现**（L226-228）；
  重跑 = 新 id + 新 seed（`+7919*(gen+1)`，L497-503）；任何丢格必须 `audit("drop", ...)`（L494-495）；
  LLM 只许输出 JSON，解析失败带上文重试，仍失败才报错（L182-193）。
- `read_sheet()` 读角色设定（`props:` 前缀单列道具，L231-252）——**钉死设定是防止角色每轮被模型重新发明**。
- 四套 system prompt：`SYSTEM`(L43)、`SYSTEM_PAGE`(L62)、`STORY_SYSTEM`(L261)、`JUDGE_SYSTEM`(L534)。
- 提示词拼装顺序（L425-444）：pose_action → tags → 角色设定/道具（逐格重复）→ page 布局标签 → LoRA trigger
  插到最前（trigger 承载身份）。
- `cmd_review()`：读 qc → 失败格排队重跑；`gen >= max_reroll` 则 drop 并审计；"渲染本身失败"（state=failed 且
  不在 qc 里）也补一次机会（L506-516）。
- `cmd_judge()`：挑"过了硬闸门但标签命中率最低"的格，可选 `--vision` 把 PNG 缩到 768 转 data URL 一起喂给模型；
  **设计上是 dry 的**，结论只写 `audit.jsonl`，绝不自己排队重跑（L576-643）。
- `cmd_finalize()`：草稿 → 正式档，同 seed、30 步 / CFG 4，新 id 加 `f`（L647-678）。

**`qc.py`（208 行）** 纯 CPU 监管层，只报告不决策。四类检查：
1. technical：可解码 / 非空白（RGB stddev < 3.0 判 flat，L95-97）/ 分辨率与任务一致（L98-103）。
2. duplicate：dHash（8×8 差分哈希，L66-78）+ 汉明距离 ≤ 4 判近重复（L161-163）。
3. **rating**：WD 评级只作为元数据写入 `qc.json`（`rating` / `rating_scores`），**不参与通过/失败判定**
   （原"非 general/safe 即硬失败"的闸门已于 v2.1 移除）。
4. tag_hit：任务 prompt 里有多少标签被确认（**建议性**，`hit==0 且可测标签≥2` 只记 `tag_hit_zero`）。
- 硬失败只有 3 类：`blank_or_flat / size_mismatch / decode_failed`。
- `tag_hit` 只在"打标器词表内的 general 标签"上统计；布局标签（`comic/2koma/4koma/multiple views/border/
  panel layout`）和 `style_*` 触发词按名字剔除（L39、L174-176），否则会出现假的 0/8 报警。

**`compose.py`（185 行）** 拼版，**不让扩散模型画字**：booru 数据训出的模型只会画伪字形，不是可读中文；
叠加真实字体是确定性方案——这也是负向词禁 `text`/`speech bubble` 的原因。
- 页面 1600×2260（B5 比例）；`fit_panel()` cover 裁剪且**重心上偏 0.35**（脸在画面中心之上，L62-70）。
- `wrap_cjk()` **逐字符**折行（CJK 无空格，`textwrap` 无效，L73-89）。
- `draw_bubble()` 圆角矩形 + 三角尾巴，尾巴 y 被夹进画格内（L171-175），戳出边框会被当渲染 bug。
- 字体候选：`workspace/assets/fonts/NotoSansSC*.ttf` → `C:\Windows\Fonts\msyh.ttc` → `simhei.ttf`（L23-28）。

**`wd_tagger.py`（143 行）** 一个模型两用：训 LoRA 时自动打标（danbooru 标签 = Anima 的提示词语言）、
QC 评级闸门。**CPU onnxruntime**，不碰 8GB 显存预算。
- 预处理自适应：按最长边补白到正方形（补 255）、BICUBIC 缩到 448、RGB→BGR，并按模型输入形状自动选
  NCHW/NHWC（L54-73）。
- 标签按 category 分类：0=general，4=character(≥0.85)，9=rating（L78-85）。

**`style_train.py`（493 行）** 五阶段闭环（`run` 串起来）：
1. `prep`：WD 自动打标 + **二次清洗**。
   二次清洗是角色 LoRA 可复用的关键：阈值 `max(2, ceil(0.8n))`，**全数据集恒定的标签 = 角色身份**，
   被丢弃并让 trigger 吸收（否则每次都要手打 "white hair, red eyes..." 才生效）；打标器噪声
   （`virtual_youtuber/watermark/...`，L171-172）也剔除；剩下的才是可变部分（姿势/场景/构图/表情）。
2. `train`：调 kohya `anima_train_network.py`。**训练前先 `c.free()` 让 ComfyUI 卸载模型腾显存**（L240-248）。
   参数：dim/alpha 32、lr 1e-4、AdamW8bit、bf16、梯度检查点、768 分辨率、batch 1、每 150 步存点，
   并开 `--cache_latents --cache_text_encoder_outputs --vae_chunk_size=64 --vae_disable_cache`（L250-283）。
   **必须 `--network_train_unet_only`**——已核对 `anima_train_network.py:65-67` 的 assert：
   缓存 TE 输出 + 训练 TE 网络会直接断言失败（官方 README 示例漏了这个 flag，照抄会失败）。
3. `convert`：kohya key → ComfyUI key（`networks/convert_anima_lora_to_comfy.py`）。
4. `probe`：每个 checkpoint 用 3 条固定探针出图，与素材集比对打分（`W_TAG/W_PALETTE/W_EDGE = 0.5/0.3/0.2`，L64）。
   公式：`total = 0.5×标签分布余弦 + 0.3×色板L1相似 + 0.2×线密度相似`；色板 L1 已按 3 通道归一化除以 6。
   实测分数：150步 0.691 / 300步 0.820 / 450步 0.815 / 600步 **0.823**（`workspace/out/probes/orig_student/_score.json`）。
5. `deploy`：最优 checkpoint → `models/loras/style_<name>.safetensors` + 写 `styles.json`。

**`make_style_dataset.py`** 不抓第三方图，**用自己的模型生成**数据集：12 姿势 × 12 场景 × 8 表情 × 6 镜头
用取模混合（L78-93），角色设定逐格重复（可学性的关键）→ 自有权、零版权风险。

**`verify_style.py`** 同 seed 各出一次"挂 LoRA / 不挂 LoRA"，比平均像素差（L89-105）。抓两种静默失败：
① key 转换错误导致 LoRA 加载了但没效果；② 强度过高淹没提示词。阈值：平均差 > 6 判 `LoRA is active`。

**`orchestrate.py`（311 行）** 总编排，全部走子进程（`subprocess.run`，cwd=pipeline），失败在 run log 里可见。
- `ensure_service()`：服务没起就用 headless 脚本拉起（`CREATE_NO_WINDOW`），最多等 180s（L58-75）。
- **`pre_ids` 快照**（L207）：只发布本次运行产生的产物，否则历史遗留草稿会被升级到正式档一起发布。
- `--page-mode`：一张图 = 一页成品（模型自己画分格），`publish_pages()` 直接拷图，不拼版不加字（L116-142）。
- 自动读 story 首行 `# 标题` 当页面标题（L78-88）。

**`tools/*.ps1`**
- `fetch.ps1`/`fetch_ms.ps1`/`fetch_mmproj.ps1`/`fetch_agent9b.ps1`：断点续传（`curl -C -`）+ stall 恢复
  + 远端长度探测（HEAD 取 Content-Length，ms/mmproj 还会退回 Content-Range）。走国内镜像
  `hf-mirror.com` / `modelscope.cn` / `gh-proxy.com`。ComfyUI 压缩包校验 SHA256（`fetch.ps1:65-77`）。
- `install.ps1`：解压 ComfyUI/MinGit/llama.cpp → 摆权重 → 建 `~/.cache/rtmlib` → `env/rtmlib_cache`
  的**目录联接**（L64-80）→ 装姿态控制 custom node → 铺 `extra_model_paths.yaml`。
- `setup_trainer.ps1`/`fix_trainer_torch.ps1`/`install_torch_cu130.ps1`：建 venv、装 torch 并**强制校验
  `sm_120` 架构**，缺失则回退 cu130 重装。
- `start_*_headless.ps1`：隐藏窗口启动，日志落 `workspace/logs/`。

---

## 三、关键设计原理（为什么这么做）

1. **CPU/GPU 时间切片**：打标、QC、LLM 全在 CPU，8GB 显存 100% 留给生图；Agent `-ngl 0` 零显存。
2. **硬规则与模型决策分离**：能写死的（分辨率、评级闸门、重跑上限、seed 偏移、id 生成、丢格审计）
   绝不交给模型 → 可审计、可复现。
3. **不可变重跑**：重跑永远新 id + 新 seed，旧产物留存，绝不静默覆盖。
4. **确定性可复现**：seed 由内容哈希决定（`seed_for`），同故事同分镜同 seed。
5. **原子状态**：`state.json` 先写 `.tmp` 再 `os.replace`，断电不会读到半截文件。
6. **站立式提示词**：Anima 的提示词语言 = danbooru 标签（空格非下划线），角色设定与道具必须逐格重复，
   否则角色/宠物换格就变种。
7. **触发词承载身份、可变项逐格写死**：LoRA trigger 只编码发型/发色/瞳色；服装配饰必须每格显式写，
   否则一换场景就漂（README 实测：夕阳近景黑西装漂成围巾毛衣）。
8. **负向词反伪影 + 代码叠字**：扩散模型只会画伪字 → 禁 `text/bubble`，对白用 Pillow 叠真字体。

---

## 四、必须注意的点

### A. 工程/移植类

1. **`D:\AnimaStudio` 硬编码在 9 个 Python 文件**（`agent/runner/qc/compose/style_train/orchestrate/
   verify_style/make_style_dataset` 各一行 `ROOT = Path(r"D:\AnimaStudio")`，加 `wd_tagger.py` 的两个
   DEFAULT_* 常量）、**全部 10 个 ps1** 的 `$Root` 默认值、`_stage/extra_model_paths.yaml` 的 `base_path`、
   `trainer/configs/*.toml` 的 `image_dir`。
   → README 说"整目录拷贝即迁移"，**实际不成立**：换盘/改名要逐个改这些常量。
2. **`install.ps1` 硬编码 `C:\Program Files\7-Zip\7z.exe`**（L4）。没装 7-Zip 时
   `$ErrorActionPreference='Continue'` 会让解压静默跳过，最终只打印一行 `python_embeded: False`。
3. **唯一站外依赖**：`%USERPROFILE%\.cache\rtmlib` 是指向 `env\rtmlib_cache` 的目录联接；删目录不能
   完全清理这一项（install.ps1 发现已存在会保留不动）。
4. **单写者假设，无文件锁**：`jobs.jsonl`/`state.json`/`qc.json` 都是"读-改-写"。`save_jobs()`
   （agent.py:211-214）整文件重写且非原子。**不要并发跑两个 runner，也不要同时跑 plan/review/finalize。**
5. **`control.json` 的 `reroll` 字段是死代码**：`agent.py control --reroll` 会写（L686-687），
   但 `runner.py` 只读 `mode`（L168-175，docstring 里"supports … reroll:<id>"是过期描述）。
   定向重跑请用 `agent.py review`/`finalize` 或手工改 `jobs.jsonl`。
6. **`jobs.jsonl` 只增不减**：`orchestrate.py` 靠 `pre_ids` 只发布本次产物；但**手工跑
   `finalize`（不带 `--ids`）会把所有 done 的 turbo 草稿全部升级**，`compose.py` 不带 `--panels`
   会把 `out/panels` 全部排进版 —— 手工操作时要自己限定 id。
7. **端口固定且仅绑 127.0.0.1**：ComfyUI 8188、Agent 8080；被占则服务起不来。
8. **ComfyUI 输出目录与 runner 强耦合（实测新发现）**：`runner.collect_output()` 只去
   `workspace/out/raw/<filename>` 找图（runner.py:126-137）。只有
   `start_comfyui_headless.ps1` 传了 `--output-directory workspace\out\raw`；**`start_comfyui.bat` 没有**，
   产物落在 `ComfyUI\output\`。
   → 若你先用 GUI 的 `start_comfyui.bat` 开了服务，再跑 `batch_run.bat`，`ensure_service()` 会认为
   "already up" 而复用它，于是**每一格都 render 成功但找不到文件**：3 次重试后 `GIVEUP`，
   而 `runner.py` 结尾仍 `return 0`，`orchestrate` 会把 "render drafts" 记成 ok，故障只在每格
   `FAIL … no output image found` 与 `state.json` 里可见。
   → 规矩：**跑批前后统一用 headless 启动器，或先关掉 GUI 版 ComfyUI。**
9. **`agent.py cmd_review` 对非 `p\d+` 的 job id 会崩**：`re.match(r"(p\d+)", jid).group(1)`（L486、L510）
   在 id 形如 `demo1`/`ref001` 时抛 `AttributeError`。
   精确边界：`qc.py` 会给 `out/panels/` 里每个 stem 建条目（实测就有 `v01`/`v01s10` 这类非 p 前缀），
   但 `cmd_review` 先做 `by_id.get(jid)`，取不到就 `continue`——**只有 `jobs.jsonl` 里也存在非 `p\d+`
   的 id 时才会崩**（即手写 job 时踩雷，光往 panels 里放图不会）。
10. **`qc.json` 是累积的**（`report = dict(qc_prev)`，qc.py:140）：历史条目永不清除，`review` 每跑一次
    都会基于旧失败记录继续累加代数（受 `--max-reroll` 限制，最终 drop + 审计，不会无限烧机）。
11. `compose.py` 顶部 `import textwrap` 未使用（L14）；docstring 里的 `--spec page.json` 参数并不存在。
12. **`probe` 的参考集 glob 依赖文件名前缀（实测新发现）**：`style_train.py:317`
    用 `REFS.glob("%s/[0-9]*.png")`。自造数据集命名 `000.png…` 没问题，但 `--train-refs` 从用户文件夹
    规整进来时会**保留原文件名**（orchestrate.py:102-111 用 `p.name`），`IMG_1234.jpg` → `IMG_1234.png`
    → glob 命中 0 张 → `np.mean([])` 得 NaN → 三个指标退化成 0.0 → 所有 checkpoint 同分 →
    `max()` 返回第一个 → **静默部署一个任意 checkpoint**。用自己素材训练时务必把参考图改成数字开头。
13. **`orchestrate` 拼版取的是"文件里最后的 N 个 f 结尾 id"**（L291-297）：reroll 的正式档
    （`p003r1f`）是后追加的，会挤掉前面的 `p001f`，且顺序不再是剧情顺序。
    实测那次运行没有 reroll，所以没暴露；有 reroll 时请用 `--panels` 显式指定顺序。
14. 训练侧两处小坑：`--discrete_flow_shift=1.0` 在 `--timestep_sampling=sigmoid` 下**不影响时间步分布**
    （库自己会打日志说明 "IGNORED"，见 library/flux_train_utils.py:576-584），它仍然作为
    FlowMatchEulerDiscreteScheduler 的 shift 生效（anima_train_network.py:257）；`instal_torch_cu130.ps1`
    写死了 `torch-2.14.0+cu130` 的 URL 与**精确字节数 1990587822**，上游一下架就下不动。
15. `fetch.ps1`/`fetch_agent9b.ps1` 的长度探测**只有 HEAD**（没有 Content-Range 回退）：
    HEAD 不给 Content-Length 时 `$want=0`，循环只能靠 stall 计数退出 → 文件其实下完了也会打印
    `GIVEUP/INCOMPLETE`。`fetch_ms.ps1`/`fetch_mmproj.ps1` 有回退，可优先用它们。
16. ~~`install.ps1` 只摆 **4B** 的 GGUF，不摆 9B 与 mmproj；而 `start_agent_headless.ps1` 默认要 9B
    （缺失时 `exit 1`）。~~
    **已修正（v2.3）**：`fetch.ps1` / `fetch_ms.ps1` / `install.ps1` 全部改指
    `Qwen3.5-9B-Q4_K_M.gguf`，4B 已从磁盘删除；mmproj 仍由 `fetch_mmproj.ps1` 单独下载
    （只有视觉 judge 需要它）。
17. 姿态控制（`anima_control_lora` + `ComfyUI-anima-pose-control` 两个 custom node、
    `anima_pose_preview2.safetensors`、两个 rtmlib ONNX 检测器）**已安装但流水线里没有任何代码调用**：
    全部 pipeline 源码与 `tools/` 都没有引用，只能按 `pipeline/references/pose_*.json` 在 GUI 里手玩。
    且它是 Preview-2 实验版、非商用权重。

### B. 硬件/资源类

18. **8GB 显存是硬约束**。OOM 降级顺序：1024² → 896² → 832² → **最后**才 `--lowvram`；
    不要随手加 `--lowvram`，它会加重 16GB 内存的换页压力。
19. **16GB 内存才是真瓶颈**。9B 权重常驻 ~5.4GB，空闲内存掉到 0.5–2GB；跑批时关掉浏览器/微信。
    只有一个大脑（9B）；内存吃紧时调小 ctx / 线程数，而不是换更小的模型（4B 已于 v2.3 删除）。
20. **训练独占显卡**（约 6GB），脚本会自动让 ComfyUI 先 `free()` 卸载模型；训练期间不要同时出图。
21. 无人值守链路每次都会尝试拉起服务并等最多 180 秒，起不来就超时返回（rc=3）。

### C. 质量/效果类

22. **Turbo 档（CFG=1）负向提示词物理上失效** → 会带伪水印/伪文字。Turbo 只用来确认构图和内容；
    分格是否出现、字是否干净都要看正式档（CFG=4 / 30 步）。
23. **page mode 是概率性的**：分格数量与边框粗细由模型决定。`--page-panels 2|4` 能大幅提高概率但不保证
    （README 实测：强制 4 格，3 页里 2 页真画出 4 格）。要精确可控就回 `compose.py` 面板拼版。
24. **vision judge 会看错**：`audit.jsonl` 里有实证——纯文本模式它反复复读指标
    （"intent coverage is zero"，正是 prompt 里禁止的行为）；开了 `--vision` 才会给具体判断
    （如 p008"猫趴在课桌上而不是在包里"），但也会误报（p006 判"穿裙子而非牛仔裤"，实际牛仔裤在）。
    **结论必须人工确认，不能当自动闸门。**
25. **自动评测指标是启发式**：标签余弦 / 色板 L1 / 线密度只衡量"像不像素材集"，**不衡量语义质量**；
    探针词表由参考素材构成，探针产出词表外的标签会被余弦忽略 → 分数可能偏乐观。
26. **LoRA 强度不要照抄数字**：`styles.json` 里 `orig_student` 用 1.0（附注：0.8 时动态姿态漂成棕发，
    同 seed A/B 得出）。换素材重训后必须 `verify_style.py` 复核再定值。
27. **剧情偶发繁体字**：小模型固有偏好，指令压不住。剧情不上画面，可手改 story 文件或接 opencc 做确定性转换。
28. **`tag_hit` 是建议性指标**：`hit=0` 只记 `tag_hit_zero`，**不判 fail**（硬失败只有 4 类）。
    别把它当质量门槛；实测 qc.json 里命中率普遍 2/2–5/6。

### D. 授权/合规类

29. **素材必须自有权利**（自己画的 / 自己 AI 生成的 / 已授权的）。不要用他人已发表作品（漫画/插画/
    动画截图）——版权问题且产出无法商用。可用 `make_style_dataset.py` 自造零风险素材。
30. **模型权重是非商用许可**（circlestone-labs-non-commercial-license，且因衍生自 Cosmos-Predict2
    另含 NVIDIA Open Model License）：**生成的图像可商用且无需署名**，但**禁止把权重托管到付费 API/平台**。
    各 LoRA 授权独立，需逐个确认。
31. ~~**内容评级闸门是写死在规则层的硬规则**，非 general/safe 一律判失败并重跑，**不是可选项**。~~
    **已于 v2.1 移除**（训练与出图均不再有评级限制）。保留一条事实供参考：WD-EVA02 **v3** 的评级标签是
    general/sensitive/questionable/explicit，旧常量里的 `safe` 是 v2 时代的标签、v3 永不输出，
    所以旧闸门实际只放行 `general`——这也是当时 `p008f`（sensitive 0.912）被误判失败的原因。
    另有历史遗留：`workspace/refs/orig_student/_rejected/` 里还留着 6 张旧闸门移出的图，
    因 `prep` 的 glob 不递归子目录，它们目前**不会**参与训练；要一并训练需手工移回上级目录再跑 prep。

### E. 本机环境类

32. 本机账户名 `f'h` 含单引号：Bash 通道会截断路径、PowerShell 回显异常，而**当前会话里 pwsh 直接不可用**
    （沙箱在 `D:\AnimaStudio` 上 `grantWrite` 失败：`SetNamedSecurityInfoW failed (Win32 5)`）。
    可靠做法：只用文件读写工具（glob/grep/read/write），或把命令结果落盘后再读文件。
33. `%USERPROFILE%` 里唯一的站外残留就是那个 rtmlib 目录联接。

---

## 五、文档 vs 代码：实测不一致清单

| # | 文档说法 | 代码实际 |
|---|----------|----------|
| 1 | README「整目录拷贝到别的盘 = 迁移完成」 | 9 个 py + 10 个 ps1 + yaml + toml 里硬编码 `D:\AnimaStudio`，必须逐个改 |
| 2 | ~~README「默认大脑 = Qwen3.5-9B，ctx 4096，16 线程」~~ | **已修正（v2.3）**：`start_agent.bat` 改指 9B 并对齐 `-t 16 -c 6144`，与 `start_agent_headless.ps1` 一致；README 的 ctx 同步为 6144 |
| 3 | `runner.py` docstring「control.json supports pause / resume / stop / reroll:<id>」 | 只消费 `mode`；`reroll` 字段无人读 |
| 4 | `compose.py` docstring 示例 `--spec page.json` | argparse 无 `--spec` |
| 5 | README「五个阶段」但只列 4 个名字 | 代码是 5 个函数（`convert` 内联在 probe/deploy 里，没有独立子命令） |
| 6 | README「免管理员、自包含」 | 解压依赖系统 7-Zip（硬编码路径），缺失时静默跳过 |
| 7 | ~~README「非 general/safe 判失败」~~ | 已随闸门移除一并改写（v2.1）；顺带确认 v3 打标器根本不产出 `safe` |

---

## 六、一句话总结

**AnimaStudio = 用"文件契约 + 子进程"把「LLM 出词 / GPU 出图 / 本地训 LoRA / 代码拼版」四条本地
流水线缝合起来的、可离线、可续跑、可审计的单文件夹漫画工厂。**
最大的工程亮点是**把一切能写死的规则（seed、id、重跑上限、丢格审计、技术校验）留在代码里，只让模型干
"创意"部分**（内容评级已于 v2.1 从规则层移除，改由调用者的提示词决定）；最大的实际风险是
**路径全硬编码、"整目录即迁移"不成立**、**ComfyUI 输出目录与 GUI 启动器
不匹配会让整批渲染静默失败**，以及**8GB 显存 + 16GB 内存的双重资源紧约束**。
