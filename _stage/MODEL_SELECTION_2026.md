# AnimaStudio 技术选型调研（2026-09）

> **调研问题**：现有出图模型 / 本地 Agent 是否满足「高质量 NSFW 漫画 + 用自己的素材训出稳定画风 + 一句话自动跑完全链路」？有没有更优解？
>
> **方法**：三条并行线——① 官方一手资料（HF 镜像 API/模型卡原文、本地 sd-scripts 官方文档）；② 两个独立子代理分别调研「出图/训练模型」与「本地 Agent/编排」；③ 本地实证（训练日志、llama.cpp 二进制能力、磁盘与文件实测）。
>
> **网络限制（影响证据边界，务必先读）**：本报告写作时本机 `web_search` 无 API key 不可用；
> `huggingface.co`／`github.com`／`civitai.com`／`reddit`／DuckDuckGo／Google **全部不可达**，
> 因此下文的"社区共识"类结论**没有一手证据**，`civitai.com` 相关的对比是缺口。
>
> **【2026-09-26 更新】Clash 代理已续费并恢复**（`127.0.0.1:7897`，实测 6–10 MB/s，比直连快 107×）。
> 上面被墙的站点现在**经代理均可访问**，报告里因网络产生的缺口已逐条补上（见下文中标注「实测补上」的条目）。
> 两个仍成立的一般性限制：`web_search` 工具本身仍无 API key（要用具体 URL 走 `web_fetch`）；
> 直连（不走代理）时 github.com 仍只有 57 KB/s。可用源仅：**`hf-mirror.com`（含 `/api/*` JSON 接口）、`raw.githubusercontent.com`、`api.github.com`、`docs.comfy.org`、`modelscope.cn`、`civitaiarchive.com`、`freedevproject.org`、`arxiv.org`、`hn.algolia.com`**。`cn.bing.com` 可用但结果被本地化过滤污染，无法用于英文技术检索。
> **因此：「模型/版本/文件大小/发布日期」来自主源 API，是硬事实；「社区共识类」结论无法一手证实，文中逐条标注。**

---

## 已实施（本轮已落进代码，2026-09-25）

本报告的 A/B/C 组建议里，**不依赖下载与联网的部分已全部实施**，改动清单见
`CODE_ANALYSIS_v2.md` 的「v2.2」。核心三项与实测验证结果：

| 改动 | 验证方式 | 结果 |
|---|---|---|
| 模型按档位自动选型 + 采样器 `er_sde/sgm_uniform` + 正式档 40 步 | 直接调用 `runner.build_workflow()` 跑三种模式 | ✅ 只装 base 时行为回退原样；模拟装上 aesthetic-v1.1 / turbo-v1.1 后，正式档自动切 Aesthetic 并**自动去掉 `score_*` 标签**、草稿自动切 Turbo 且不再挂 LoRA |
| 训练 `lr 1e-4→2e-5`、`600→1200 步`、加 `--qwen_image_vae_2d`、删死参数 `--discrete_flow_shift` | 读 `style_train.py --help` + 导入自检 | ✅ 默认值已生效 |
| Agent 接入 JSON Schema 约束解码 | **起独立 llama-server（当时 4B 尚在，端口 8089）实发两次请求对比** | ✅ **抓到真实故障**：不加约束时 4B 返回 `{"decision": "re-render"}`（键名错 → `cmd_judge` 会因 `invalid verdict` 静默跳过该格）；加 `response_format` 后稳定返回 `{"verdict": "reroll", "reason": ...}`，键名与取值均正确 |

| 删除 4B 大脑，只留 9B | 全盘 Glob + 引用扫描 | ✅ `tools\gguf\Qwen3-4B-Q4_K_M.gguf` 已删除（释放 2.33 GiB，无残留副本）；`fetch.ps1` / `fetch_ms.ps1` / `install.ps1` / 两个启动器 / README 全部改指 `Qwen3.5-9B-Q4_K_M.gguf`；`start_agent.bat` 的上下文从 16384 降到 6144、线程 12→16，与 headless 版对齐 |

| 可选思考模式 `--think`（默认关闭） | 在 8080 的运行实例上实测开/关同一问题 | ✅ 关闭 8.6 s / 20 token 干净作答；打开 66 s / 300 token 且 `finish_reason=length`、`content` 为空 —— 这就是"只翻开关会让 JSON 解析连续失败"的直接证据。已实现为默认关闭的开关：预算封顶 1024、`max_tokens` 自动 +1024、ctx 无需加大 |

`er_sde` / `sgm_uniform` 已在本机 ComfyUI 核实存在（`comfy/samplers.py:975`、`:1367`）。

**尚未实施的（需要下载或装节点）**：Anima-Aesthetic v1.1 / Turbo v1.1 权重下载、`Anima-InContext-Character`
（跨格角色一致）、把已装好的 LLLite 姿态控制接进 `runner`、QC 三件套 ONNX 检测器、
Forge 可靠性代理层（已核实真实存在：2250★/MIT/2026-09-01 仍在更新）。

---

## 结论摘要

| 你的要求 | 现状判定 | 依据 |
|---|---|---|
| 输出**高质量 NSFW 漫画** | ✅ **满足，但有前提**。Anima 原生支持 `nsfw`/`explicit` 安全标签，HF 上已有多个 Anima NSFW LoRA 与 NSFW 融合模型；但你不该指望它画对白文字 | 官方模型卡 Safety tags；HF 实测到 `GAi92/anima-base-1-loras-nsfw`、`Hoshikuchi/Hoshilicious-Anima`、`funnycat1/RAMTHRUST-NSFW-PINK-ALCHEMY-ANIMA`(来自 Civitai)、`ling0322/libwaifu-anima-turbo-v1.1`、`BlueSkyXN/Anima-base-loras` |
| 用**自己上传的素材**训出**一致画风** | ✅ **满足，且你的链路已经是正确配方**；但有 3 个参数没按官方建议配，白扔了效果与速度 | 本地 `docs/anima_train_network.md`；Anima 官方微调忠告；你的 `train_orig_student.log` 实证 |
| **一句话 → 剧本 → 出图 → 监管** | ✅ **已实现且有产出物**（story.txt → jobs.jsonl → 20 格 → qc → judge → 拼版页均存在）。瓶颈不是"模型不够大"，而是**缺约束解码与工具调用护栏** | `workspace/jobs/*`、`audit.jsonl`、`workspace/logs/run_*.log` 实证；llama.cpp 本机已支持 `--json-schema` |
| 上传剧本/素材 → 自己拿去训练 + 出剧情 | ✅ 已实现（`--train-refs` / `--auto-story` / `--premise`） | `orchestrate.py` 参数与 stage 链 |

**总判定：不建议更换主力模型。** 8GB 显存 + 16GB 内存 + danbooru 标签流水线 + 非商用权重/可商用产出这一组合下，**Anima 是最优解**；真正该做的是 **9 项零成本或低成本的参数/模型升级**（见第四节），其中 3 项能直接提升成图质量，2 项能把训练质量与速度拉开差距，2 项能显著提高 Agent 可靠性。

---

## 一、出图模型：Anima 实测定位

### 1.1 它到底是什么（官方一手）

| 项 | 事实 | 来源 |
|---|---|---|
| 参数/基座 | **2B** 文生图，基座 `nvidia/Cosmos-Predict2-2B-TextToImage` | [模型卡](https://hf-mirror.com/circlestone-labs/Anima/raw/main/README.md) |
| 架构 | MiniTrainDIT 系 DiT + Rectified Flow；TE = **Qwen3-0.6B**；**LLM Adapter**（6 层 transformer，把 Qwen3 嵌入桥接到 T5 兼容 cross-attention 空间）；VAE = Qwen-Image VAE（16ch / 8× 下采样） | 本地 [docs/anima_train_network.md](D:\AnimaStudio\trainer\sd-scripts\docs\anima_train_network.md) |
| 数据 | 数百万动漫图 + ~80 万非动漫艺术图，**无合成数据**，动漫知识截止 **2025-09** | 模型卡 |
| 分辨率 | **512²–1536²**（硬上限），30–50 步 / CFG 4–5 | 模型卡 |
| 标签语言 | **原生 danbooru**：小写、**空格非下划线**（score 标签例外）；质量标签**两套并存**（人类分 + PonyV7 美学分 `score_9…score_1`）；画师标签**必须加 `@` 前缀**，"否则效果会非常弱" | 模型卡 |
| 安全标签 | **`safe, sensitive, nsfw, explicit`** —— NSFW 是原生词表 | 模型卡 |
| 生态 | HF **2.29k likes / 116.8 万下载**（2026-01-29 发布，8 个月）；与 **Comfy Org 官方合作** | HF API |
| 许可 | **CircleStone Labs 非商用 v1.2** + NVIDIA Open Model License。**模型/LoRA 非商用；生成图明确可商用**（卖图/约稿/付费产品概念图均可）；个人可出售自训 LoRA 权重（§2c）；禁止拿模型开付费 API/付费平台/嵌进商业化产品 | [LICENSE.md](https://hf-mirror.com/circlestone-labs/Anima/raw/main/LICENSE.md) |

### 1.2 官方版本清单（HF 文件树实测）

仓库内实际存在（你目前只用了 base-v1.0 + 一个**旧版 turbo LoRA v0.2**）：

```
split_files/diffusion_models/
  anima-base-v1.0.safetensors        ← 你现在用的（官方指定：只用于训 LoRA）
  anima-aesthetic-v1.0.safetensors
  anima-aesthetic-v1.0b.safetensors
  anima-aesthetic-v1.1.safetensors   ← 更新版，官方称"一致性/默认画风更好"
  anima-turbo-v1.0.safetensors
  anima-turbo-v1.1.safetensors       ← 蒸馏版 v1.1
  anima-preview.safetensors / preview2 / preview3-base
```

官方原话：**"I recommend starting with Anima-Turbo"**（比 Aesthetic 只差一点但极快）；**Aesthetic 平均质量高于 Turbo**；**Base 是给训 LoRA 用的**（默认画风 "very plain and neutral"）。
→ **你现在的正式档走的是 Base + 30 步**，这正是"画风平淡"的根因：该用 **Aesthetic** 出正式档。这是本次调研里**性价比最高的一条**。

### 1.3 已知弱点（官方自承，与你的漫画目标直接相关）

1. **长文本渲染差**：单词/短句可以，长句不行 → **漫画对白绝不能指望模型直出**。你现在用 Pillow 叠字是**正确路线**，应加固而非替换。
2. **多角色会 confusion**：只列角色名不描述外观会出错 → 分格图应"一格少角色"或显式描述外观。
3. **不做写实**（刻意设计）。
4. **`--fp8_base` 不支持**（训练中会被警告并禁用）。

### 1.4 NSFW 能力判定

- **原生支持**：`nsfw` / `explicit` 属官方安全标签词表，模型卡 Limitations 只说"短提示词可能生成非预期内容，建议用 safety 标签 + 详细提示词控向"——**立场中立，非加固对齐**。
- **生态确实存在**（HF 实测）：NSFW LoRA（`GAi92/anima-base-1-loras-nsfw`、`GAi92/anima-preview-3-loras-nsfw`、`mpasila/Anima-LoRAs`、`thojm/hyperfusion_lora_anima`、`BlueSkyXN/Anima-base-loras`）、NSFW 融合/微调（`Hoshikuchi/Hoshilicious-Anima`、`ling0322/libwaifu-anima-turbo-v1.1`、`funnycat1/RAMTHRUST-NSFW-PINK-ALCHEMY-ANIMA` + `RunningHubAI/...v2.9-unet`）。
- **重要操作细节**：官方标签顺序是 `[quality/meta/year/**safety**] [1girl…] [character] [series] [artist] [general]` → **NSFW 标签要放最前面**（安全标签区）。你已按上一轮请求删掉了提示词里强制的 `safe`，现在**需要在 jobs 的 prompt 里显式写 `nsfw` 或 `explicit`**，否则模型没有方向。
- ✅ **【2026-09-26 实测补上，原为"最大证据缺口"】Civitai 生态对比**（代理恢复后 civitai.com API 可达；
  注意 `baseModels=` 过滤现在是**生效**的，与写报告时不同；另外 API 只给游标分页、**没有总数**，
  Python 请求必须带浏览器 User-Agent，否则 403）：

  | 基础模型 | LoRA 总数 | 同深度（约第 1000 条）游标 `下载\|点赞` |
  |---|---|---|
  | Anima | >4000 | **74 \| 655** |
  | NoobAI | >4000 | 50 \| 441 |
  | SDXL 1.0 | >4000 | 235 \| 1549 |
  | Pony | >4000 | 414 \| 2604 |
  | **Illustrious** | >4000 | **453 \| 3214** |

  **结论**：四个生态**数量上都超过 4000 条**（Anima 并不小），但**深度差别很大**——排到第 1000 位时，
  Illustrious 的 LoRA 仍有 453 下载/3214 赞，而 **Anima 已降到 74/655**。
  也就是说 **Anima 的生态"有量但不厚"**：可用条目足够多，但高互动、高口碑的那一层比 Illustrious 薄得多。

  **对你的实际意义**：这**验证了你自训画风的路线是对的**。Anima 上能直接拿来用的现成风格远少于
  Illustrious，所以「自己训 `orig_student` / `sodalord_style`」不是绕路，而是这个生态里的必要手段。
  同时也不必考虑迁移到 Illustrious——它虽然生态更厚，但**更慢**，且已训 LoRA 全废（见下方否定结论）。

---

## 二、训练链路：你已经对了，但 3 个参数白扔了

### 2.1 你那次训练的真实数据（来自 `train_orig_student.log`）

- 失败 3 次后才成功：① `ModuleNotFoundError: torchvision` → ② TOML `resolution` 写成 `768,768` 触发 `TomlDecodeError` → ③ **`AssertionError: network for Text Encoder cannot be trained with caching Text Encoder outputs`**（即之前分析里说的"必须 `--network_train_unet_only`"，你确实踩过）。
- 成功配置：dim 32 / alpha 32 / **lr 1e-4** / AdamW8bit / cosine_with_restarts / warmup 30 / **sigmoid** / **discrete_flow_shift 1.0** / 600 步 / 每 150 步存点 / bf16 / 梯度检查点 / `cache_latents` / `cache_text_encoder_outputs` / `vae_chunk_size=64` / `vae_disable_cache`。
- 实测：**108 样本/epoch × 6 epoch，600 步约 21 分钟（2.1–3.0 s/it），avr_loss 0.099 → 0.0902**，无 NaN、无 OOM。**这个配方本身是健康的。**

### 2.2 三个该改的地方

| # | 问题 | 证据 | 改法 |
|---|---|---|---|
| 1 | **`--discrete_flow_shift=1.0` 完全无效**（死参数） | **你自己的日志第 176-179 行就写着** `discrete_flow_shift=1.0 (IGNORED for timestep_sampling='sigmoid')`；官方文档第 492 行："only applies when `--timestep_sampling` is set to `shift`" | 删掉它，或改用 `--timestep_sampling shift` 才让它生效 |
| 2 | **学习率偏高约 5 倍** | Anima 官方微调忠告："**Use a low learning rate. For a rank 32 LoRA, start with 2e-5**"；你用的是 **1e-4**。注意 sd-scripts 官方示例里的 1e-4 配的是 `--network_dim=8`，且原文明说"just an example" | 下次训练试 **`--lr 2e-5`**，步数相应上调（如 1200–1500）。这大概率能同时改善"强度必须拉到 1.0 才不漂"的问题 |
| 3 | **没用 `--qwen_image_vae_2d`** | 官方文档：该选项让 VAE encode/decode **约快 2 倍、峰值 VRAM 约 1/3**（RTX3090 1024²×10 张：4.4GB/7.7s → 1.4GB/4.5s），且**明确"推荐用于 latent 缓存"**；启用后 `--vae_chunk_size` 影响很小、`--vae_disable_cache` 变成 no-op | 加 `--qwen_image_vae_2d`，并可删掉 `--vae_disable_cache --vae_chunk_size=64` |

### 2.3 已经正确、不要动的地方（避免瞎折腾）

- **没训 LLM adapter**：官方忠告"Don't train the LLM adapter"。本地 `networks/lora_anima.py:241` 默认 `train_llm_adapter=false`，`ANIMA_ADAPTER_TARGET_REPLACE_MODULE` 仅在显式开启时才加入 → **你天然就对了**。
- **用 Anima-Base 训 LoRA**：官方明确"LoRAs should be trained using this version" → 正确。
- **dim/alpha 32、768 分辨率、batch 1、AdamW8bit、bf16 + 梯度检查点**：与官方/社区低显存配方一致。
- **`--network_train_unet_only` + TE 输出缓存**：唯一可行的省显存组合，已踩对。
- **prep 的"二次清洗"**（恒定标签＝身份，丢给 trigger 吸收）：这是让角色 LoRA 可复用的关键，设计正确。

---

## 三、有没有更优解？候选横向对比

| 候选 | 架构/参数 | 8GB 可行性 | 生成图商用 | danbooru 标签 | NSFW | 判定 |
|---|---|---|---|---|---|---|
| **Anima**（现役） | DiT 2B + Qwen3-0.6B | ✅ 原生，1024²/30步约 40s | ✅ 明文可商用 | ✅ 原生 | ✅ 原生标签 + 已有 NSFW LoRA/融合模型 | **继续做主力的最优解** |
| Anima-**Aesthetic v1.1** | 同上 | ✅ | ✅ | ✅ | ✅ | **同一模型的新版本，正式档应该换它** |
| **Anima-2.9B**（社区） | 28→**40 层**，~2.9B；额外 170 万样本，知识截止 **2026-07**；官方 ComfyUI+Forge-Neo 支持 | ⚠️ 需 ComfyUI ≥0.33.1 或装配套节点；显存需实测 | 同 Anima 许可 | ✅ 同上 | 未证实（但同源） | ⚠️ **值得试**：画风/构图/时效性明确更好；风险是**你的 kohya 训练链路能否跟到 2.9B 未证实**，建议先在 GUI 里 A/B |
| Illustrious / WAI / NoobAI | SDXL ~3.5B + 双 TE | ✅ 可跑但**明显更慢** | WAI 继承 SDXL；**NoobAI 模型卡明文禁止商用"model-generated products"**（与上游 FaiPL 冲突，实务按严者处理） | ✅ | ✅ 存量最厚 | ❌ **不建议迁移**：慢、许可更麻烦、已训 LoRA 全废 |
| Pony V7 | **AuraFlow ~10B** | ⚠️ 需 Q8 GGUF + offload | 受限（禁推理服务/营收 >1M 等） | ❌ 自然语言 | 训练数据**已过滤 explicit** | ❌ |
| Chroma1-HD | FLUX.1-schnell MMDiT **8.9B** | ⚠️ 需 GGUF/int8 | Apache-2.0 但与上游 FLUX AUP 有冲突（**未证实**） | ❌ | ✅ 未加固 | ❌ 太重 |
| **Z-Anime**（Z-Image 全量微调） | S3-DiT **6B**，Apache 2.0 | ⚠️ **自称 8GB 可跑**（fp8 ~6GB / GGUF Q4 4.2GB / Q8 6.73GB），但 16GB 内存要同时扛 4B 文本编码器 | ✅ **Apache 2.0，最干净** | ❌ **明说"自然语言最佳，不是标签列表"** | ⚠️ **自称"partially NSFW capable"** | ⚠️ **唯一的架构级替代候选**。优点：许可最松、**原生中英文字渲染**、8 步/4 步蒸馏变体。缺点：标签语言不兼容（你的 agent + WD tagger QC 全要改）、内存吃紧、NSFW 只是"部分" |
| Mage-Flow（微软） | MIT 许可 | 未证实 | ✅ MIT | ✅ 有 danbooru 微调（`RicemanT/MageTrail`） | 未证实 | ⚠️ 观察名单 |

**判定：不迁移。** 理由按权重：① 许可是候选里唯一把"模型非商用、**产出明确可商用**"写进正文的；② 2B 是你 40s/1024² 速度的根本原因，换 SDXL 显著变慢、换 6–9B 直接吃紧；③ 训练器三线原生支持（kohya / diffusion-pipe / ai-toolkit），**零迁移成本**；④ danbooru 标签原生，整条 agent+QC 流水线原样复用。

---

## 四、立刻可做的升级清单（按 ROI 排序）

### A. 出图侧（零代码/一行改动，直接提质）

1. **正式档换 `anima-aesthetic-v1.1.safetensors`**（官方：默认画风与一致性更好）。改 `runner.py` 的模板或让模型名可配。
2. **草稿档换 `anima-turbo-v1.1.safetensors`**（替代 base + 旧版 turbo-LoRA-v0.2 的组合），官方配置 **CFG 1 / 8–12 步** —— 比你现在 10 步/CFG1 更规范。
3. **换采样器/调度器**：你现在是 `euler + simple`。官方推荐 **`er_sde`（默认首选）/ `euler_a` / `dpmpp_2m_sde_gpu`**；Anima-2.9B 作者推荐 **`euler + sgm-uniform`**（构图/细节平衡），重构图用 `res-multistep + linear-quadratic`。→ 正式档建议 **`er_sde` + `sgm_uniform`，40–50 步**（官方区间 30–50，你现在取的是下限 30）。
4. **画师标签必须加 `@`**：官方原文 "You must put `@` in front of the artist. **The effect will be very weak if you don't.**" → 这是**不训练就能大幅改变画风**的免费杠杆，建议在 agent 的提示词体系里支持 `@artist` 标签位。
5. **NSFW 方向靠标签而非负向词**：把 `nsfw` / `explicit` 放在提示词**最前面**（安全标签区），同时**注意别再让 `safe` 出现在正向词里**（你上一轮已删掉 POS_PREFIX 里的 `safe`，方向正确）。
6. **启用你已装好但完全没用起来的两件武器**：
   - **Anima LLLite（`kohya-ss/Anima-LLLite`，231 likes）+ 姿态控制**：custom node、`anima_pose_preview2.safetensors`、两个 rtmlib 检测器**都已经装好了，但整个 pipeline 零调用**。跨格姿态/构图稳定性靠它，比反复抽卡便宜得多。
   - **`darask0/Anima-InContext-Character`**：**参考图驱动角色一致性，免训练**。把角色的全身照 + 脸部特写各一张当参考，新姿势/新场景/新表情都能保持身份（rank 64，~99.4 万图 / 6.2 万角色对训练）。作者的推荐参数恰好是 `er_sde / simple, 30 步, CFG 4, discrete_flow_shift 3.0`。→ **这是解决"漫画跨格角色一致"最直接的工具**，优先级高于再训一个角色 LoRA。
7. **多角色分格**：官方承认会 confusion → 一格少角色，用 `compose.py` 拼版而不是让模型在一张图里画多格多人（你现在的 page mode 正是这个风险点）。

### B. 训练侧

8. 见 2.2 的三条：**删 `--discrete_flow_shift`、lr 改 2e-5、加 `--qwen_image_vae_2d`**。
9. 若训练 OOM：官方给出 `--blocks_to_swap`（**28 层模型上限 26**）与 `--unsloth_offload_checkpointing`（比 `--cpu_offload_checkpointing` 快，但二者与 `blocks_to_swap` **互斥**）。注意**你的真瓶颈是 16GB 内存**，swap/offload 都吃 CPU RAM。
10. 可选：`--network_args "verbose=True"` 打印实际 LoRA 模块与维度；`rank_dropout=0.1` / `loraplus_lr_ratio=2.0` 作为过拟合与收敛的调节旋钮。

### C. Agent 侧（用你本机二进制已支持的能力，消掉自写重试逻辑）

本机实测：`llama-server.exe` **version 0.5.0-dev (build 11177, commit 1ab7e5ad2)**，已支持：

| 能力 | 参数（实测存在） | 用途 |
|---|---|---|
| **约束解码（JSON Schema）** | `-j, --json-schema SCHEMA`、`-jf, --json-schema-file FILE` | **直接消掉 `llm_json()` 的"解析失败→重试"主路径**。注意：约束解码只保证"按构造可解析且符合 schema"，**不保证取值正确**——省下的工程预算应转投到**取值校验** |
| GBNF 语法 | `--grammar`、`--grammar-file` | 需要比 JSON Schema 更细的控制时用 |
| **推理预算** | `--reasoning-budget N`（默认 -1 无限制，**0 = 立即结束**）、`-rea/--reasoning [on\|off\|auto]` | 你现在只用 `chat_template_kwargs={"enable_thinking": false}` 关思考。**子代理报告称新版 build 上 Qwen3.5 可能因无界 reasoning budget 在 llama-server 上挂死**——本机 build 11177 有 `--reasoning-budget`，建议**显式加 `--reasoning-budget 0` 做双保险**（这一条我无法在本机复现挂死，属预防性建议） |
| KV cache 量化 | `-ctk / -ctv` | 16GB 内存下把 KV 存成 q8_0，给上下文腾空间 |
| MoE 专家 offload | `-ncmoe / --n-cpu-moe` | 若将来尝试 MoE 可用；但**容量问题解决不了**（见下） |

11. **接一个可靠性代理层**（可选）：子代理调研到 [antoinezambelli/forge](https://github.com/antoinezambelli/forge)（MIT，llama-server 后端明确受支持，护栏含**专门抢救 Qwen 的 `<tool_call>` XML**、tool call 形状校验、失败重试）。**仅把 base_url 从 `:8080/v1` 换到 `:8081/v1`**。我未能独立验证该仓库细节（github.com 网页不可达），列为**待你验证的候选**。
12. **不要试图换更大的模型**（这条是明确的否定性结论）：Qwen3.5-35B-A3B / Qwen3.6-35B-A3B 这类 MoE 的**最低可用量化（IQ1_M）已 11.37GB**，Q4 是 22.7GB；Qwen3.8-27B 的 Q2_K_XL 已 9.83GB。**MoE 省的是算力不是容量**——全部专家权重仍须驻留内存。16GB 机器上 9B 就是正确档位（原作为回退的 4B 已于 v2.3 删除）。（附：子代理提到的"实测 5.68GB"与你记的 5.4GB 不矛盾，是 GB 与 GiB 单位差异；我本地实测该文件为 **5.29 GiB = 5.68 GB**。）
13. **可选：换成"无审查"版本的同一模型**。HF 上存在同尺寸替代（`HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive` 2139 likes / 71.6 万下载；`DavidAU/Qwen3.5-9B-...-Uncensored-...-GGUF` 807 likes / 169 万下载，标签含 fiction/roleplaying）——**同样 5.3GB 内存占用**，写成人向剧本时不会被"礼貌化"。风险：微调可能损伤 JSON 纪律，**必须 A/B 后再换**。
14. **QC 增强：补三个 ONNX 检测器**（都可在 CPU/零显存跑）——`ogkalu/comic-text-and-bubble-detector`（RT-DETR-v2，5.7 万下载）判"不该出现的文字/气泡"、`TareHimself/comic-text-mask`（支持 zh/ja/ko 文字分割）、`mosesb/best-comic-panel-detection`（YOLOv12 分格检测）。**拼起来就是"分格结构 + 气泡定位 + 文字存在性"的漫画专用 QC 三件套**，正好补上你现在只有 WD 标签/技术校验的缺口。

---

## 五、明确的否定性结论（省时间）

- ❌ **不要迁移到 SDXL 系**：更慢、NoobAI 许可明文禁止商用产出、已训 LoRA 全废。
- ❌ **不要迁移到 Z-Anime / Chroma / Qwen-Image**：6–9B 在你的 16GB 内存 + 8GB 显存上要么跑不动、要么把内存吃干；Z-Anime 还要求改用自然语言提示（你的 agent + WD tagger 全要改）。**Z-Anime 值得放进观察名单**（Apache 2.0 + 中文文字渲染），但不是现在。
- ❌ **不要指望模型画中文对白**：官方承认长文本渲染差。你的 Pillow 叠字路线是对的，应加固（气泡定位 + 独立图层输出，便于改台词不重出图）。
- ⚠️ **【2026-09-26 实测更正】原结论"不要把 Agent 挪到 GPU"已被推翻**。当时的前提是"Agent 与扩散模型
  需要同时占显存"，但这条流水线的阶段是**串行**的——plan/judge 阶段不渲染，render 阶段不用 LLM，
  所以显存可以**分时复用**。实测（llama.cpp b11195 CUDA 13.4 / RTX 5070 Laptop / sm_120）：

  | 配置 | prefill | decode | 视觉 judge |
  |---|---|---|---|
  | CPU（9B Q4 留在 RAM） | 86.7 tok/s | 4.52 tok/s | 12.7 秒/格 |
  | GPU `-ngl 99`（无 mmproj） | 944.0 | 38.80 | ✗ 挤掉了视觉 |
  | GPU `-ngl 28` + mmproj | 789.7 | 21.23 | 2.2 秒/格 |
  | **GPU `-ngl 99` + mmproj**（2026-09-27 复核） | **103**\* | **41.86** | **在位** |

  \* prefill 那栏只有 11 个 prompt token，样本太小不可比；decode 41.86 是 128 token 的实测。
  小字：`-ngl 99 --mmproj` 是**挤得进**的——整卡 8151MiB，ComfyUI 彻底 /free 后有 6608MiB 可用，
  9B+视觉投影加载后占用 7605MiB（余 287MiB）。之前判"全量卸载会挤掉视觉"，是因为当时没有先把
  ComfyUI 完全交出来。所以生产配置改成 **`-ngl 99 --mmproj`**，视觉不但没挤掉，decode 还比
  `-ngl 28` 快一倍。余量只有 287MiB，这恰恰说明**必须**做阶段交接、两者绝不能同时在卡上。
  ⚠️ 注意：**CUDA 构建即使 `-ngl 0` 也占约 1.1GB 显存**（CUDA 上下文+计算缓冲，实测 1028→2384MiB）。
  所以低显存回退必须换成无 CUDA 运行时的 CPU 构建（`tools\llamacpp-cpu\`），而不是把层数设 0。

  折算：40 页一轮的 AI 开销从约 **70 分钟降到约 8 分钟**；代价是每次阶段切换要停/启 LLM
  （实测冷启动 14 秒、停止 2.8 秒），一轮约 5-6 次 ≈ 1.5-2 分钟。构建在 `tools\llamacpp\`
  （CUDA，build 11195），CPU 备份在 `tools\llamacpp-cpu\`（build 11177），
  **必须 CUDA 13.4，12.4 不支持 sm_120**。
  ✅ **已完成**：启动器按空闲显存自动选构建并验活、失败自动回退 CPU；
  orchestrate 的阶段边界交接已实现（阶段用 `needs="llm"|"gpu"` 声明），
  实测交接全程通过（`_stage\test_handoff.py`）。
- ❌ **不要换 Ollama**：你现在用 llama-server 是正确的（子代理引 Forge 数据：顶级配置几乎全在 llama-server 上）。
- ⚠️ **不要照抄"换更大 MoE 就变聪明"**：容量卡死，见 C-12。

---

## 六、本次调研的证据边界（哪些没能证实）

1. **Civitai 上 Anima vs Illustrious 的 LoRA 存量对比**——civitai.com 不可达，`civitaiarchive.com` 的 `baseModel=` 过滤不生效。**这是最大缺口**；能确定的只有"两边生态都在快速迭代"（Anima 在 2026-09 单月新增数十个模型/适配器，含 NSFW 与量化版本）。
2. **r/StableDiffusion、r/comfyui 的社区横评**——全部不可达，所有"社区共识"类结论均无一手证据。
3. **Anima-2.9B 的训练兼容性**——模型真实存在（我直接抓到模型卡：385 likes、3.49 万下载、40 层、额外 170 万样本、截止 2026-07、官方 ComfyUI/Forge-Neo 支持），但**能否用你现有 kohya 链路训 LoRA 未证实**。（注：子代理曾判定它"仅见于内容农场、疑为第三方分支"，**此结论有误**，以本报告的直接抓取为准。）
4. **Z-Anime / Z-Image 的 NSFW 实际质量**——官方只写 "partially NSFW capable"，无实测数据。
5. **`--reasoning-budget` 挂死风险**——本机 build 11177 存在该参数，但无法复现挂死；列为预防性配置。
6. **`--qwen_image_vae_2d` 在你机器上的实测加速比**——官方给的是 RTX3090 数据，你的卡未知。

---

## 附：一句话总结

**你的三件套没有选错，方向也对；缺的不是"更强的模型"，而是"把已有模型和工具链里没打开的能力打开"**——正式档换 Aesthetic v1.1、草稿换 Turbo v1.1、采样器换 `er_sde` + 40–50 步、提示词补 `@artist` 与安全性标签、训练 lr 改 2e-5 并加 `--qwen_image_vae_2d`、把已装好的 LLLite 姿态控制与 InContext 角色参考真正接进流水线、Agent 侧改用 `--json-schema` 约束解码替代重试解析。这些加起来是**一两天的工作量**，而换架构是**一两周且要重训所有 LoRA**。
