# AnimaStudio 源码分析报告

> 分析范围：`pipeline/` 全部 Python 源码（13 个）、`tools/` 全部 PowerShell 脚本（10 个）、
> 根目录启动器（3 个 .bat）、`_stage/extra_model_paths.yaml`、`trainer/configs/*.toml`、
> `workspace/jobs/*`（jobs/state/qc/audit/styles）与 README.txt。
> 结论基于源码逐行阅读，非推测。

---

## 一、这是什么

一套 **全本地、可离线、单文件夹自包含的动漫漫画（条漫/分格漫画）生产流水线**。

三条腿：

| 腿 | 组件 | 干什么 | 资源 |
|----|------|--------|------|
| 大脑 | llama.cpp + Qwen3.5-9B（**4B 已于 v2.3 删除**） | 出剧情、拆分镜、写提示词、监管产出 | **CPU，-ngl 0，零显存** |
| 画笔 | ComfyUI + Anima 2B DiT（Qwen3-0.6B 文本编码器 + Qwen Image VAE） | 出分格图 / 整页图 | **GPU，8GB 贴线，峰值 ~6.8GB** |
| 教练 | kohya sd-scripts（anima_train_network.py）+ WD-EVA02 打标器 | 用自有素材训画风/角色 LoRA，自动评测选点 | GPU（训练时独占） |

设计口号：**删掉 `D:\AnimaStudio` 即完全卸载；整目录拷贝即迁移**。日常出图与训练全程不联网。

三个入口：
- `start_comfyui.bat` → 图形界面出图（http://127.0.0.1:8188）
- `batch_run.bat` → 无人值守全链路（故事 → 成品页）
- `python pipeline\style_train.py run --name X` → 训练自己的 LoRA

---

## 二、核心原理：一个"文件契约驱动的状态机"

整个项目**没有数据库、没有消息队列、没有常驻服务编排框架**。它靠
`workspace/jobs/` 下的几个文件在进程之间传递状态，每个进程只做一件事、写完就退出。
这是理解全部代码的钥匙。

### 2.1 六个契约文件

| 文件 | 角色 | 写者 | 读者 |
|------|------|------|------|
| `jobs.jsonl` | **任务队列**，每行一格（提示词/尺寸/seed/对白/标签/LoRA） | agent.py `plan`/`review`/`finalize` | runner.py、qc.py、compose.py |
| `control.json` | **控制意图** `{"mode":"run\|pause\|stop"}` | agent.py `control` | runner.py（每轮读一次） |
| `state.json` | **断点续跑状态**（已完成的不重复出图） | runner.py（原子替换写） | runner.py、agent.py |
| `qc.json` | **质检报告**（技术校验 + 内容评级 + 标签命中） | qc.py | agent.py `review`/`judge` |
| `audit.jsonl` | **决策追溯**（agent 所有动作留痕，只追加） | agent.py | 人 |
| `styles.json` | **LoRA 注册表**（trigger/文件/强度/评分） | style_train.py `deploy` | agent.py `plan` |

### 2.2 主数据流

```
story.txt
   │  agent.py plan  （LLM 拆格 → 提示词）
   ▼
jobs.jsonl ──► runner.py ──► ComfyUI(8188) ──► out/raw/*.png
   │              │                                  │
   │              └─ state.json 记录 done/failed      ▼
   │                                          out/panels/<id>.png
   │  agent.py review ◄── qc.json ◄── qc.py（CPU：解码/空白/分辨率/dHash/评级闸门/标签命中）
   │        │
   │        └─► 新 job id + 新 seed（重跑）写回 jobs.jsonl
   ▼
agent.py finalize（草稿 → 正式档，同 seed，30步/CFG4）
   ▼
runner.py（重出正式档）──► compose.py（拼版 + 中文气泡）──► out/pages/page_*.png
```

### 2.3 模块逐一说明

#### `comfy.py` — 极简 ComfyUI API 客户端
- **只用标准库 urllib**，因为要跑在 ComfyUI 自带的 embedded python（`python_embeded`）上，不能装额外依赖。
- `ready()` 探活 `/system_stats`；`vram_free()` 读显存；`free()` 让 ComfyUI 卸载模型腾显存。
- `submit()` 里有个**关键 hack**：提交前过滤掉所有非节点键（只保留有 `class_type` 的 dict）。因为 ComfyUI 的 `validate_prompt` 会对每个顶层条目调 `.get()`，任何注释类键都会导致 HTTP 500。
- `free()` **不能解析 JSON**：ComfyUI 对 `/free` 返回空 body。
- `wait()` 轮询 `/history/<id>`，同时采样最小空闲显存（用于报告峰值占用）。

#### `runner.py` — 渲染执行器（render-only）
三条设计准则（源码注释明确写出）：
1. **只渲染**：QC 单独一轮跑，让 CPU 打标器永远不和 GPU 渲染抢资源 —— 这就是项目自称的 "time slicing"。
2. **可续跑**：每个完成任务写 `state.json`（`os.replace` 原子替换），杀进程重启不会重出已完成的图。
3. **可控**：每格开跑前读 `control.json`，支持 pause/stop。**Agent 只能写意图，不能杀进程**。

`build_workflow(job)` 用**节点 ID 硬编码**改工作流模板 `templates/anima_t2i.json`：
- 节点 1 UNETLoader，2 CLIPLoader(Qwen3-0.6B)，3 VAELoader，10 LoraLoaderModelOnly，20 正向，21 负向，30 尺寸，40 KSampler，50 VAEDecode，60 SaveImage。
- 三种模式：
  - `turbo=true` → 挂 Turbo LoRA，10 步 / CFG 1.0，KSampler.model 指向节点 10。
  - `style_lora` 存在 → 挂画风 LoRA，30 步 / CFG 4.0。
  - 否则 → 裸模型，30 步 / CFG 4.0。
- 正向词自动补 `POS_PREFIX`；负向词有一套**反伪影块**：`watermark, signature, logo, text, english text, japanese text, twitter username, web address, copyright name, dated, speech bubble` —— 因为 booru 系模型爱自己画角标/伪字/签名。

#### `agent.py` — 本地 LLM 的三种职责（最核心、最长，32KB）
职责：出词(`plan`/`story`)、控制(`control`)、监管(`review`/`judge`)、收尾(`finalize`)。

**最重要的设计哲学：硬规则写死在代码里，不交给模型。**
- 每次重跑 = **新 job id + 新 seed**（`sid + 7919*(gen+1)`），绝不静默覆盖。
- 任何一格被丢弃必须写 `audit.jsonl`。
- LLM **只允许输出 JSON**；解析失败 → 带上下文重试 → 仍失败才报错。
- `split_beats()` 在**代码里**把故事切成 N 段，防止模型漏剧情点。
- `CHUNK = 4`：每次 LLM 调用只规划 4 格，保持 JSON 短小可解析。

关键函数：
- `seed_for(text, salt)`：用 sha256 哈希（镜头+场景+job id）→ 确定性 seed，**同输入永远同 seed，可复现**。
- `read_sheet()`：读角色设定文件（`props:` 前缀单独归入道具）；**钉死角色设定是防止角色每轮被模型重新发明**的手段。
- `SYSTEM` / `SYSTEM_PAGE` / `STORY_SYSTEM` / `JUDGE_SYSTEM`：四套 system prompt，规定 danbooru 风格、空格非下划线、每格重复设定、动作不得重复上一格、台词必须简体中文且 ≤22 字等。
- `cmd_review()`：读 qc.json，对失败格排队重跑；`gen >= max_reroll` 则 drop 并审计；对"渲染本身失败"的格也给第二次机会。
- `cmd_judge()`：**模型驱动的画面复核**。只挑"过了硬闸门但标签命中率最低"的格子，可选 `--vision` 把 PNG 缩放成 data URL 一起喂给模型。**设计上是 dry 的**：只把结论写进 `audit.jsonl`，绝不自己排队重跑。
- `cmd_finalize()`：草稿 → 正式档，**同 seed**、30 步 / CFG 4，新 id 加 `f` 后缀。

#### `qc.py` — 监管层（纯 CPU，只报告不决策）
四类检查：
1. **technical**：能解码、非空白（标准差 < 3.0 判 flat）、分辨率与任务一致。
2. **duplicate**：dHash（差分哈希）+ 汉明距离 ≤ 4 判近重复。
3. **rating gate**：WD 打标器评级必须是 `general`/`safe`，否则**硬失败**（写死在规则层，不是模型说了算）。
4. **tag hit**：任务要求的提示词里有多少被打标器确认（**建议性**，不判失败）。

关键细节：`tag_hit` 只在"打标器词表内的 general 标签"上统计；布局标签（`2koma`/`multiple views` 等）和 `style_*` 触发词被剔除，否则会产生假的 `0/8` 报警。

**硬规则 vs 建议性**的边界写死在 `hard = [r for r in reasons if r.startswith((...))]`：只有 blank/size/decode/rating 四类能判 fail。

#### `compose.py` — 拼版（分格 + 中文气泡）
- 为什么不让扩散模型画字：booru 数据训出的模型只会画**伪字形**，不是可读中文。所以叠加真实字体是确定性、可读的方案 —— 这也是负向词禁 `text`/`bubble` 的原因。
- 页面 B5 比例 1600×2260；`fit_panel()` cover 裁剪且**重心上偏 0.35**（脸在中心之上）。
- `wrap_cjk()`：**逐字符**折行（CJK 无空格，`textwrap` 失效）。
- `draw_bubble()`：圆角矩形 + 三角尾巴，尾巴自动夹在画格内（尾巴戳出边框会被当渲染 bug）。
- 字体候选：`workspace/assets/fonts/NotoSansSC*.ttf` → `C:\Windows\Fonts\msyh.ttc` → `simhei.ttf`。

#### `wd_tagger.py` — WD-EVA02 打标器（一个模型两用）
- **CPU ONNX（onnxruntime）**，绝不碰 8GB 显存预算。
- 用途 ① 训 LoRA 时自动打标（danbooru 标签 = Anima 的提示词语言）；用途 ② QC 的内容评级闸门。
- 预处理自适应：先按最长边补白到正方形（补 255 白）、BICUBIC 缩到 448、RGB→BGR，并按模型输入形状自动决定 NCHW/NHWC。
- 标签按 category 分类：0=general，4=character，9=rating。

#### `style_train.py` — 风格训练闭环（五阶段，可单跑可全跑）
1. **prep**：评级闸门（非 general/safe 自动移入 `_rejected`）+ WD 自动打标 + **二次清洗**。
   - 二次清洗是让角色 LoRA 可复用的关键：**全数据集恒定的标签 = 角色身份**，被丢弃并让 trigger 词吸收（否则每次都要手打 "white hair, red eyes..." 才生效）；打标器噪声（vtuber/watermark 幻觉）也剔除。剩下的才是可变部分（姿势/场景/构图/表情）。
2. **train**：调 kohya `anima_train_network.py`。**训练前先 `c.free()` 让 ComfyUI 卸载模型腾显存**（单卡 8GB 是独占资源）。参数：network_dim/alpha 32、lr 1e-4、AdamW8bit、bf16、梯度检查点、768 分辨率、batch 1、每 150 步存点，并开 `--cache_latents --cache_text_encoder_outputs --vae_chunk_size=64`。
   - **必须 `--network_train_unet_only`**：anima_train_network.py 若"既训练文本编码器又缓存其输出"会 assert（官方 README 示例漏了这个 flag，照抄会失败）。
3. **convert**：kohya LoRA key → ComfyUI key（`networks/convert_anima_lora_to_comfy.py`）。
4. **probe**：每个 checkpoint 用固定探针集（3 条）出图，与素材集比对打分，选最优。
5. **deploy**：最优 checkpoint 转成 `models/loras/style_<name>.safetensors` 并写 `styles.json`。

**评分公式（三个廉价 CPU 指标，不靠感觉）**：
```
total = 0.5 × 标签分布余弦相似度 + 0.3 × 色板L1相似度 + 0.2 × 线密度相似度
```
（色板 L1 已按 3 通道归一化除以 6，落在 0..1；标签向量单位化后点积 = 余弦。）

#### `make_style_dataset.py` — 自造数据集
不抓第三方图，而是**用自己的模型生成**一套：12 姿势 × 12 场景 × 8 表情 × 6 镜头做笛卡尔混合，角色设定逐格重复（这是可学性的关键）。产出**自有权、零版权风险**的数据集。

#### `verify_style.py` — LoRA 有效性验证
同 seed 各出一次"挂 LoRA" / "不挂 LoRA"，比平均像素差。抓住两种静默失败：① key 转换错误导致 LoRA 加载了但没效果；② 强度过高淹没提示词。阈值：平均差 > 6 判"LoRA is active"。

#### `check_weights.py` / `test_agent.py` / `test_first_light.py`
- 权重完整性校验（safetensors 头解析、张量数、dtype 分布）。
- Agent 冒烟测试：可达性 + 纯 CPU + 严格 JSON 纪律。
- first light：跑一张图并报告耗时与显存峰值。

#### `orchestrate.py` — 总编排
把上面所有阶段串成子进程（`subprocess.run`，cwd=pipeline），保证失败在 run log 里可见而非被吞。
关键点：
- `ensure_service()`：服务没起就以 headless 方式拉起（隐藏窗口），并等待就绪（最多 180s）。
- **`pre_ids`**：跑前抓一份现有 job id 快照，**只发布本次运行产生的产物**，否则历史遗留草稿会被升级到正式档一起发布。
- 自动读 story 首行 `# 标题` 作为页面标题。
- `--page-mode`：一张图 = 一页成品（模型自己画分格），走 `publish_pages()` 直接拷图，不拼版不加字。

#### `tools/*.ps1` — 安装/下载/启动
- `fetch.ps1` / `fetch_ms.ps1` / `fetch_mmproj.ps1` / `fetch_agent9b.ps1`：带**断点续传 + stall 恢复 + 远端长度探测**（HEAD 拿 Content-Length，失败退回 Content-Range）的下载器。走国内镜像：`hf-mirror.com`、`modelscope.cn`、`gh-proxy.com`。ComfyUI 压缩包校验 SHA256。
- `install.ps1`：解压 ComfyUI/MinGit/llama.cpp，把权重摆到 `models/`，建 `~/.cache/rtmlib` → `env/rtmlib_cache` 的**目录联接**(junction)，装姿态控制 custom node，铺 `extra_model_paths.yaml`。
- `setup_trainer.ps1` / `fix_trainer_torch.ps1` / `install_torch_cu130.ps1`：建 trainer venv，装 torch 并**强制校验 `sm_120` 架构**（新显卡需要 cu130 构建），否则回退重装。
- `start_*_headless.ps1`：隐藏窗口启动，日志落 `workspace/logs/`。

---

## 三、关键设计原理（为什么这么做）

1. **CPU/GPU 时间切片**：打标、QC、LLM 全在 CPU，8GB 显存 100% 留给生图。Agent 用 `-ngl 0` 实现零显存占用。
2. **硬规则与模型决策分离**：能写死的（分辨率、评级闸门、重跑次数、seed 偏移、id 生成、丢格审计）绝不交给模型 → 可审计、可复现。
3. **不可变重跑**：重跑永远是新 id + 新 seed，旧产物留存，绝不静默覆盖。
4. **确定性可复现**：seed 由内容哈希决定（`seed_for`），同故事同分镜同 seed。
5. **原子状态**：`state.json` 先写 `.tmp` 再 `os.replace`，断电也不会读到半截文件。
6. **站立式提示词**：Anima 的提示词语言 = danbooru 标签（空格非下划线），角色设定与道具必须逐格重复，否则角色/宠物换格就变种。
7. **触发词承载身份、可变项逐格写死**：LoRA trigger 只编码发型/发色/瞳色；服装配饰必须在每格显式写，否则一换场景就漂。
8. **负向词反伪影**：扩散模型只会画伪字，所以禁 text/bubble，成品页不放对白（page mode 是设计选择，非缺陷）。

---

## 四、必须注意的点（按重要性排序）

### A. 工程/移植类

1. **`D:\AnimaStudio` 被硬编码在几乎每个脚本里**（agent/runner/qc/compose/wd_tagger/style_train/orchestrate/verify/make_style_dataset 都有 `ROOT = Path(r"D:\AnimaStudio")`，PowerShell 脚本默认 `$Root='D:\AnimaStudio'`）。
   → README 说"整目录拷贝即迁移"，但**实际不成立**：迁到别的盘/改名后必须逐个改这些常量。这是文档与现实最大的出入。
2. **`install.ps1` 依赖 `C:\Program Files\7-Zip\7z.exe` 硬编码**，没装 7-Zip 会静默跳过解压。
3. **唯一站外依赖**：`%USERPROFILE%\.cache\rtmlib` 是指向 `env\rtmlib_cache` 的目录联接。删目录不能完全清理这一项。
4. **单写者假设，无文件锁**：`jobs.jsonl`/`state.json`/`qc.json` 都是"读-改-写"。若同时跑两个 runner，`state.json` 会互相覆盖。**不要并发跑 `runner.py`**。
5. **`control.json` 的 `reroll` 字段是死代码**：`agent.py control --reroll` 会把它写进去，但 **`runner.py` 只读 `mode`，从不消费 `reroll`**。要定向重跑请用 `agent.py review`/`finalize` 或手工改 `jobs.jsonl`。
6. **`jobs.jsonl` 只增不减**：历史任务会累积。`orchestrate.py` 靠 `pre_ids` 快照区分本次运行；**手工直接跑 `finalize`/`compose` 时要注意别把旧产物一起发布**。
7. **端口固定并仅绑 127.0.0.1**：ComfyUI 8188、Agent 8080。端口被占则服务起不来。
8. `compose.py` 顶部 `import textwrap` 未使用（无害）。

### B. 硬件/资源类

9. **8GB 显存是硬约束**。OOM 降级顺序：1024² → 896² → 832² → **最后**才 `--lowvram`。不要随手加 `--lowvram`，它会加重 16GB 内存的换页压力。
10. **16GB 内存才是真瓶颈**。9B 大脑权重常驻 5.4GB，空闲内存会掉到 0.5–2GB。跑批时**关掉浏览器和微信**（"吃紧时回退 4B"一条已于 v2.3 作废：4B 已删除）。
11. **训练独占显卡，期间不要同时出图**（脚本会自动让 ComfyUI 先卸载模型）。
12. 无人值守链路每次都会**尝试拉起服务并等最多 180 秒**，服务起不来会超时返回。

### C. 质量/效果类（容易踩的坑）

13. **Turbo 档（CFG=1）负向提示词物理上失效** → 会带伪水印/伪文字。Turbo 只能用来确认构图和内容，**分格是否出现、字是否干净都要看正式档（CFG=4/30步）**。
14. **page mode 是概率性的**：分格数量、边框粗细由模型决定，不可精确控制。`--page-panels 2|4` 能大幅提高概率但不保证（实测强制 4 格：3 页里 2 页真画出 4 格）。要精确可控就回到 `compose.py` 面板拼版。
15. **vision judge 会看错**：它可能报出具体但错误的缺陷（例：判"穿裙子而非牛仔裤"，实际牛仔裤在、只是旁边有裙子残影）。**它的结论必须人工确认，不能当自动闸门**。
16. **自动评测指标是启发式**：标签余弦 / 色板 L1 / 线密度只衡量"像不像素材集"，**不衡量语义质量**。且 probe 的标签词表只由参考素材构成，探针产出词表外的标签会被余弦忽略 → 分数可能偏乐观。
17. **LoRA 强度不要照抄数字**：styles.json 里 `orig_student` 用 1.0（0.8 时动态姿态会漂成棕发，同 seed A/B 得出）。换素材重训后必须用 `verify_style.py` 复核再定值。
18. **剧情偶发繁体字**：小模型固有偏好，指令压不住。剧情不上画面，可手工改 story 文件，或接 opencc 做确定性转换。
19. **`tag_hit` 是建议性指标**，`hit=0` 会记 `tag_hit_zero` 但**不判 fail**（硬失败只有 4 类）。别把 tag_hit 当成质量门槛。

### D. 授权/合规类

20. **素材必须自有权利**（自己画的 / 自己 AI 生成的 / 已授权的）。**不要用他人已发表作品**（漫画/插画/动画截图）——版权问题且产出无法商用。可用 `make_style_dataset.py` 自造零风险素材。
21. **模型权重是非商用许可**（circlestone-labs-non-commercial-license）：**生成的图像可商用且无需署名**，但**禁止把权重托管到付费 API/平台**。各 LoRA 授权独立，需逐个确认。
22. **内容评级闸门是写死在规则层的硬规则**，非 general/safe 一律判失败并重跑。**这不是可选项**。
    > ⚠️ 已过期：该项已于 v2.1 移除（`qc.py` / `style_train.py` / `runner.py` / `agent.py` / README 同步），
    > 训练与出图均不再做评级限制。见 `CODE_ANALYSIS_v2.md` 的「变更记录」。

### E. 本机环境类（与代码无关但影响操作）

23. 本机账户名 `f'h` 含单引号，导致 **Bash 通道故障**（路径被截断）、**PowerShell 输出回显为空**。可靠执行方式：`PowerShell 写结果到文件 → 用读取工具读文件`，或清理代理环境变量后 `Start-Process` 调 venv Python 把输出落盘。

---

## 五、快速上手命令（对照 README 校正）

```bat
REM 图形出图
start_comfyui.bat                      → http://127.0.0.1:8188

REM 无人值守全链路（故事→成品页）
batch_run.bat --story workspace\jobs\story.txt --panels 8 --title 秘密

REM 复用已训 LoRA + 自编剧情
batch_run.bat --story workspace\jobs\story.txt --style orig_student ^
    --sheet workspace\jobs\orig_student_sheet.txt --panels 4

REM 只给一句前提，让本地模型自编剧情
batch_run.bat --style orig_student --premise "转学第一天遇到一只猫" --panels 4

REM 一张图 = 一页（page mode）
batch_run.bat --page-mode --style orig_student --premise "..." --panels 2

REM 训练闭环
python pipeline\style_train.py run --name my_style --steps 600
python pipeline\verify_style.py --name my_style --strength 1.0

REM 手工分段跑
python pipeline\agent.py plan --story workspace\jobs\story.txt --panels 8 --draft
python pipeline\runner.py
python pipeline\qc.py
python pipeline\agent.py review
python pipeline\agent.py finalize
python pipeline\runner.py
python pipeline\compose.py --cols 2 --rows 4 --panels p001f,p002f --title 标题
```

---

## 六、一句话总结

**AnimaStudio = 用"文件契约 + 子进程"把「LLM 出词 / GPU 出图 / 本地训 LoRA」三条本地流水线缝合起来的、可离线、可续跑、可审计的单文件夹漫画工厂。** 它最大的工程亮点是**把一切能写死的规则（评级闸门、seed、id、重跑上限、丢格审计）都留在代码里，只让模型干"创意"部分**；最大的实际风险是**路径全硬编码、"整目录即迁移"并不成立**，以及**8GB 显存 + 16GB 内存的双重资源紧约束**。
