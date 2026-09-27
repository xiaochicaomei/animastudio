# AnimaStudio 使用指南

> 安装与模型下载见 [`README.txt`](README.txt)；设计取舍见 [`README.md`](README.md)。

---

## 一、三个进程，各管一段

| 进程 | 端口 | 作用 | 谁启动 |
|---|---|---|---|
| **ComfyUI** | 8188 | 出图（Anima DiT） | **你手动启动，必须** |
| **agent（llama.cpp）** | 8080 | 写剧情、排版、审查、裁决 | `orchestrate.py` **自动启停** |
| kohya trainer | — | 只在训画风时用 | `style_train.py` 自动拉子进程 |

**你只需要手动启动 ComfyUI。** agent 由主流程按需拉起和关掉——因为 9B 和出图模型抢同一张
8 GB 卡，两者不能同时驻留。

```powershell
# 前台窗口（能看实时日志）
.\start_comfyui.bat

# 后台无窗口（推荐）
powershell -ExecutionPolicy Bypass -File tools\start_comfyui_headless.ps1
```

两者都已带显存标志 `--enable-dynamic-vram --async-offload --vram-headroom 0.5`。
**没有它们 2.9B 的 hires 精修放不下**（实测：裸跑 OOM，加上后仍余约 1 GiB）。

---

## 二、最小可用流程

```powershell
# 1. 起 ComfyUI
.\start_comfyui.bat

# 2. 一句话 → 完整漫画
.\batch_run.bat --premise "雨夜，独居的女人给淋湿的邻居开门" --panels 8
```

产物在 `workspace\out\pages\`。

`batch_run.bat` 就是 `pipeline\orchestrate.py` 的包装，**参数完全一样**，只是替你调好
python 路径。

---

## 三、主命令：`orchestrate.py`

### 3.1 输入方式（三选一）

```powershell
# A. 给一句话，模型自己写剧情（最省事）
--premise "一句话前提"

# B. 自己写好剧情文件
--story "D:\path\story.txt"

# C. 先交互式起草，再拿来跑
python pipeline\agent.py story --out story.txt --panels 40
```

剧情文件格式 —— **一行一个镜头**，只写"镜头能拍到的东西"：

```
# 雨夜          ← 第一个 # 开头的是标题
秀子站在厨房水槽前洗碗，窗外的雨敲打着玻璃
年轻邻居敲响后门，浑身湿透
秀子递给他一条干毛巾
```

### 3.2 出图模式（最重要的选择）

```powershell
--page-mode                        # 一张图 = 一整页，模型自己排 2~4 格（默认）
--no-page-mode --cols 1 --rows 1   # 一图一格，每页一个满分辨率画面
--no-page-mode --cols 2 --rows 2   # 一图一格，代码拼成 2×2
```

**实测对比**（同样 4 次渲染、10.5 分钟，两边总像素完全相同）：

| | 总像素 | 画面数 | 每画面 |
|---|---|---|---|
| 整页模式 | 9.1 MP | 10 个 | **0.91 MP** |
| **逐格模式** | 9.1 MP | 4 个 | **2.28 MP** |

**整页模式还会退化**：模型拿到"2 格"时经常把同一个镜头画两遍（实测那页两格都是同一个背影、
同一个水槽）。逐格模式四格是四个真正不同的镜头。

**逐格还顺带解决文字问题**：气泡标签只在整页模式插入，逐格模式下模型根本不画气泡，
伪字从构造上不可能出现。

### 3.3 画风与角色

```powershell
--style komi              # 用已注册的画风 LoRA（它会同时决定用哪个底模）
--sheet xiaozi_sheet.txt  # 钉住人物设定，不让模型每页重新发明
```

人物设定文件就是一行 danbooru 标签：

```
mature female, milf, short dark purple hair, bob cut, purple eyes, wide hips, large breasts, mature face
```

> **一条硬约束**：`--style`（2.9B 训出的）与 `--ref-img`（2B 的 InContext 零件）**互斥**。
> 同时加会**静默掉到 base 底模** —— 既不是 2.9B，画风 LoRA 也挂不上（它按 40 层训练），
> 而且全程不报错。这是最危险的一个坑，因为它看起来是成功的。

### 3.4 内容强度

```powershell
--adult
```

不开的话，9B 会把冲突激烈的前提写成纯情文艺片：**40 个 beat 全在写氛围，从"他领她走向卧室"
直接跳到"她回头满眼泪水"**，关键时刻整个被跳切掉。开启后决定性 beat 占比从 30% 升到 64%。

原因不是有过滤器（闸门在 v2.1 就拆干净了），而是**剧情阶段从来没人要求过**。

### 3.5 精修（hires）

```powershell
--hires 1.5                 # 潜空间放大 1.5×，只对正式档生效，草稿不受影响
--hires-denoise 0.25        # 0.40 会重画手，0.25 保得住
--hires-method bislerp      # 默认 nearest-exact 有块状感，bislerp 是社区默认
```

**2.9B 建议不开 hires**：实测它会把 2.9B 赖以取胜的硬边赛璐璐线条**抹柔** ——
买来的分辨率和丢掉的锐度互相抵消。2B 路线则建议开。

### 3.6 让 AI 判断画质

```powershell
--judge                    # 默认开：视觉审查
--judge-limit 40           # 审多少张（约 13 秒/张；40 张约 9 分钟）
--judge-reroll             # 默认开：判定 reroll 就重出一张
--judge-reroll-max 4       # 重出上限
--judge-think-below 0.5    # 只在意图覆盖率低于此值时让 judge 思考
```

judge 是**带视觉的**（需要 agent 启动时带 `--mmproj`），判词具体到可核对：

```
p038 reroll  角色没有按提示低头而是正视观众，且手部细节缺失
p037 reroll  画面中只有两名角色，但意图要求包含单名成熟女性角色
p036 reroll  表情看起来在微笑，与要求呆滞表情的指令不符
p035 keep    准确呈现了双手拉扯裙摆、脸朝下以及成熟女性角色的设定
```

---

## 四、完整配方

```powershell
# ① 快速试水（2B + 画风 + 精修，8 页约 10 分钟）
.\batch_run.bat --premise "..." --panels 8 --style sodalord_style `
                --sheet workspace\jobs\xiaozi_sheet.txt `
                --adult --hires 1.5 --hires-denoise 0.25

# ② 正式出书（2.9B + 画风，逐格单页，40 页约 1 小时）
.\batch_run.bat --premise "..." --panels 40 --style komi `
                --sheet workspace\jobs\xiaozi_sheet.txt `
                --adult --no-page-mode --cols 1 --rows 1 `
                --judge-limit 40 --clean-queue

# ③ 只要草稿看构图（很快，不精修）
.\batch_run.bat --premise "..." --panels 8 --skip-final

# ④ 断点续跑：什么都不用加，重跑同样的命令
```

**续跑机制**：进度写在 `workspace\jobs\state.json`，已完成的不会重做。

**但要注意**：`runner.py` 会渲染**所有**未完成任务，包括上次运行的残留 —— 按旧画风、
旧底模重出一遍，然后在发布阶段被丢弃。**开新项目时加 `--clean-queue`**（把旧契约
**归档**而非删除）。不加的话启动会警告你有多少残留、都是哪些 id。

---

## 五、训练自己的画风

### 5.1 准备素材

```
workspace\refs\<画风名>\    ← 图片放这里（PNG/JPG/WEBP 都行）
```

流程会自动转 PNG 并按内容哈希去重。

### 5.2 一条命令跑完：预处理 → 训练 → 评测 → 部署

```powershell
.\run_style_train.bat run --name komi --base 2.9b `
    --resolution 512 --workers 0 --steps 1200 --save-every 300
```

| 参数 | 说明 |
|---|---|
| `--base 2.9b` | 训练目标底模。**必须和推理时一致**，否则 LoRA 落在错层（不报错但静默劣化） |
| `--resolution 512` | 512 比 768 快近 3 倍（实测 8.09 vs 22.3 秒/步） |
| `--workers 0` | **必须**。kohya 默认 8 个 DataLoader worker 各持一份 latent 缓存约 8 GB，16 GB 机器会只剩 0.1 GiB |
| `--steps` | 1200 步 ≈ 0.64 epoch（313 张素材）。嫌画风不够像就加到 2000 |

**实测参考**：313 张素材、1200 步、512px = **7.4 小时**，产出 250 MB/份的 LoRA。

### 5.3 分步执行

```powershell
python pipeline\style_train.py prep   --name komi                    # 只打标签
python pipeline\style_train.py train  --name komi --base 2.9b --steps 1200
python pipeline\style_train.py probe  --name komi --base 2.9b        # 只评测已有 checkpoint
python pipeline\style_train.py deploy --name komi --base 2.9b        # 只部署
```

`run` 会自动 probe：对每个 checkpoint 出 3 张图，按「标签分布 / 色板 / 线密度」三项打分，
**选最好的一版部署**。这一步不用你挑。

---

## 六、单步调试（跳过主流程）

```powershell
# 写剧情
python pipeline\agent.py story --premise "..." --panels 8 --out story.txt --adult

# 排版（只生成任务，不出图）
python pipeline\agent.py plan --story story.txt --panels 8 --style komi `
       --sheet sheet.txt --draft

# 出图（渲染队列里所有未完成任务）
python pipeline\runner.py

# 审查已出的图
python pipeline\agent.py review

# 拼版 + 写字
python pipeline\compose.py --cols 2 --rows 2 --panels p001,p002,p003,p004 --out page.png
```

独立小工具：

```powershell
python pipeline\presets.py --list                # 全部预设
python pipeline\presets.py --list --nsfw         # 受限池
python pipeline\presets.py --artists 40          # 模型最认的 40 位画师（按训练语料帖数）
python pipeline\presets.py --negatives           # 负向包
python pipeline\check_weights.py                 # 权重完整性检查
```

**预设库需要先生成**（它不进仓库）：

```powershell
python pipeline\build_presets.py --kit <AnimaPromptKit 目录>
```

---

## 七、产物在哪里

```
workspace\
  out\
    raw\         ← ComfyUI 原始输出（每张图都留档）
    panels\      ← 单格正式档 p001f.png、重出档 p001r1f.png
    pages\       ← 成品页 page_001.png   ← 你要的东西
  jobs\
    jobs.jsonl   ← 任务队列
    state.json   ← 完成状态（续跑靠它）
    qc.json      ← 质检结果
    audit.jsonl  ← 所有 agent 决策记录（含 judge 判词）
    styles.json  ← 已注册画风（含各自的 base 底模）
  logs\
    run_*.log    ← 每次运行的总日志   ← 排查问题先看这个
    runner.log   ← 出图日志（每张耗时）
    agent.log    ← LLM 日志
    agent-launch.log ← agent 用哪个构建启动（判断有没有掉 CPU）
    comfyui.log  ← ComfyUI 日志
  refs\          ← 训练素材
```

---

## 八、最容易踩的坑

| 现象 | 原因 | 处理 |
|---|---|---|
| **整轮 LLM 慢 9 倍** | agent 掉回 CPU 构建 | 看 `logs\agent-launch.log`：`exe=llamacpp-cpu` 就是掉了。先确认 ComfyUI 已让出显存再重启 |
| **多出一批旧画风的图** | 队列里有上次运行的残留 | 加 `--clean-queue` |
| **画风没生效，图变差但不报错** | LoRA 层数与底模不匹配 | 检查 `workspace\jobs\styles.json` 里该画风的 `base` 字段 |
| **显存不足 OOM** | 用了不带显存标志的启动方式 | 用 `start_comfyui.bat`（已带）或 `tools\start_comfyui_headless.ps1` |
| **人物每页长得不一样** | 没给 `--sheet` | 加人物设定文件 |
| **台词变成日文** | 日式题材下模型会漂 | 提示词里已点名禁止；若仍出现请提单 |
| **2.9B 的画变柔了** | 开了 hires | 2.9B 建议不开，它的优势就是硬线条 |

---

## 九、日常速查

```powershell
# 起环境
.\start_comfyui.bat

# 出一篇
.\batch_run.bat --premise "..." --panels 40 --adult `
                --style komi --sheet workspace\jobs\xiaozi_sheet.txt `
                --no-page-mode --cols 1 --rows 1 --clean-queue

# 训一个新画风
.\run_style_train.bat run --name <名字> --base 2.9b `
    --resolution 512 --workers 0 --steps 1200

# 手动腾显存（agent 会占约 6.3 GiB）
powershell -File tools\stop_agent.ps1
```

---

## 十、为什么有些代码看起来"过度谨慎"

这些常量都不是猜的，是在这台机器上量出来的。改之前请先看对应的注释。

- **2.9B 是 40 层 DiT，2B 家族是 28 层。** 2B 的 LoRA 挂到 40 层模型上**不报错**，
  只是落在错层。所以适配器按**记录的底模**路由，绝不靠推断。
- **`comfy_yield` 读 `nvidia-smi`，不读 ComfyUI 自报的空闲显存。** 两者能差 6 GiB
  （ComfyUI 报 6.3 GiB 空闲时 `nvidia-smi` 只有 184 MiB，因为 torch 保留着已释放的块）。
  读错来源会让 LLM 掉到 CPU 构建、整轮慢 9 倍，而日志里只留一行字。
- **约束解码下 schema 是硬约束，不是提示词。** JSON schema 里没有的字段模型根本发不出来，
  提示词怎么写都没用（`additionalProperties: false` 让它成为结构性的）。
- **`runner.py` 会渲染所有未完成任务**，不只是本次的。所以有 `--clean-queue` 和启动警告。
- **负向词里点名 `outline / sticker / cutout`。** 2.9B 会给人物画一圈白色描边（贴纸感），
  提示词里没有任何标签要求它这样做，只能在负向词里点名——实测同 seed 下白边完全消失。
