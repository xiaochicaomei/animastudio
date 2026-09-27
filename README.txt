================================================================
 AnimaStudio 操作手册
 本地 AI 漫画工作台（单文件夹、可离线、免管理员）
================================================================

一、这是什么
----------------------------------------------------------------
一套全本地的动漫漫画生产流水线：
  本地 LLM Agent（GPU）→ 出提示词 / 控制流水线 / 监管产出
  本地生图（GPU）      → Anima 2B / 2.9B 模型出分格图
  显存交接             → 一张 8GB 卡，两者按阶段轮流用（见第八节）
  本地训练闭环         → 自己的素材训 LoRA，自动评测选点
  本地拼版             → 分格 + 中文对白气泡成页

全部落在 D:\AnimaStudio 一个目录内：
  删除该目录 = 完全卸载；整目录拷贝到别的盘 = 迁移完成。
装好之后，**日常出图与训练全程不需要联网**。


二、三个入口
----------------------------------------------------------------
1) 图形界面出图
     双击  D:\AnimaStudio\start_comfyui.bat
     浏览器打开  http://127.0.0.1:8188
     模板库里搜 “Anima Base v1” 即可加载官方工作流

2) 无人值守跑完整条流水线（故事 → 成品页）
     双击  D:\AnimaStudio\batch_run.bat
     或命令行：
       batch_run.bat --story "D:\AnimaStudio\workspace\jobs\story.txt" --panels 8 --title 秘密
     它会自动：拉起服务 → Agent 拆分镜 → 出草稿 → QC 闸门 → Agent 复核
                → 转正式档 → 重出 → 拼版成页，全程写日志到 workspace\logs\

3) 训练自己的画风 / 角色 LoRA
     D:\AnimaStudio\workspace\refs\<名字>\  放素材（见第六节素材要求）
     然后：
       python pipeline\style_train.py run --name <名字> --steps 600
     它会自动：自动打标 → 训练 → 转 ComfyUI 格式
                → 逐 checkpoint 探针评测 → 部署最优到 models\loras\style_<名字>.safetensors


三、出图参数档位（本机实测）
----------------------------------------------------------------
  档位        分辨率      步数/CFG              耗时      用途
  Turbo       1024²       10 步 / CFG 1         7-10 秒   构图迭代、快速试分镜
  正式        1024²       40 步 / CFG 4         实测 49 秒 正式出图
  竖版        832×1216    40 步 / CFG 4         约 45 秒  条漫分格
  正式+两段   1536²       40 步 / CFG 4         实测 2 分钟 正式出图（hires，推荐）

  两段式精修（hires）：一段出图 → 潜空间放大 → 二段低 denoise 精修。需要时才开：
    batch_run.bat --story workspace\jobs\story.txt --panels 4 --hires 1.5
  实测（1024² 基准、同种子 4242）：单段 49 秒 → 两段 126-166 秒，产出 1536×1536，
  细节（发丝、衣褶、耳饰、背景物件）明显增加，8GB 显存未 OOM。
  denoise 是关键旋钮，实测对比：0.40 会把手指和领口重画糊，0.25 手部干净且细节仍增
  → 默认已设为 0.25。要改就用 --hires-denoise（如 --hires-denoise 0.2 更贴一段结构、
  细节略少；0.37 是社区参考工作流的值）。
  阶梯：--hires 是倍率（1.5 推荐），--hires-denoise 是二段重绘幅度（0.25 推荐）。
  只作用于正式档（草稿保持快）；比例建议 1.5（1024²→1536²）。2.0 会超过 Anima 的
  1536² 推荐包线，runner 会打 WARNING 但仍然会尝试。

  提示词杠杆（来自社区提示词工具包，已抽成预设库，需要时才开）：
    预设库由 pipeline\build_presets.py 从 AnimaPromptKit 的数据文件 + 你的服饰标签文件
    生成：466 条预设 / 400 位画师 / 433 条画师配方 / 8 个负向包 / 14 组成人标签。
    浏览：python pipeline\presets.py --list [--nsfw]
          python pipeline\presets.py --artists 40     最认的画师（按训练语料帖数）
          python pipeline\presets.py --negatives      负向包
          python pipeline\presets.py --show 雨中回眸   看某条预设的内容

    --artist 名称    加 @画师 标签。模型卡称这是最强的画风杠杆，不加 @ 效果"非常弱"。
                     表里的 posts 是该画师在 Anima 训练语料里的图量——越高模型越认。
                     例：--artist "qp:flapper"
    --preset 名称    钉一整块现成标签 + 它自带的英文自然语言描述（模型卡要求自然语言写细）。
                     例：--preset 雨中回眸（成人向的先 --list --nsfw 查名字）
    --negative-pack  用 kit 里已验证的负向包替换内置默认；反伪影块仍会追加，留白页也
                     依然禁气泡（与默认规则一致）。例：--negative-pack 文字水印

    实测：三个杠杆同时用，规划 78 秒成功；@画师落在提示词最前、预设标签并入词表、
    自然语言句接在末尾、负向词按台词有无正确分流。

  采样器/调度器默认已改为 er_sde + sgm_uniform（Anima 作者推荐组合，原先
  是 euler + simple）。正式档步数从 30 提到 40（官方区间是 30-50）。

  权重档位：草稿优先用 anima-turbo-v1.1，正式档优先用 anima-aesthetic-v1.1，
  找不到时自动回退 anima-base-v1.0（+ turbo LoRA），所以不装新权重也能跑。
  官方原话：Base 是用来训 LoRA 的、默认画风 very plain and neutral；Aesthetic
  的一致性与默认画风更好。换成 Aesthetic 后它按官方建议不再使用 score_* 质量
  标签（正向/负向前缀会自动切换）。

  重要：Turbo 档是 CFG=1，**负向提示词在物理上不生效**。
  所以 Turbo 出的图可能带伪水印/伪文字，正式档才会被负向词压掉。
  标准流程就是：Turbo 打草稿 → 选定后走正式档。

  分辨率支持 512² ~ 1536²。8GB 显存贴线运行，峰值占用约 6.8GB。
  若某张 OOM，按此顺序降级：1024² → 896² → 832² → 最后才加 --lowvram。


四、控制与监管（Agent 的三块职责）
----------------------------------------------------------------
  出词   workspace\jobs\jobs.jsonl      每行一格：提示词/尺寸/seed/对白/标签
  控制   workspace\jobs\control.json    {"mode":"run|pause|stop"}  Agent 只能写意图，
                                        不能杀进程；runner 每轮读取并服从
  监管   workspace\jobs\qc.json         QC 报告：技术校验 + 内容评级 + 标签命中
         workspace\jobs\audit.jsonl     Agent 所有决策的追溯记录
         workspace\jobs\state.json      断点续跑状态（已完成的不重复出图）

  监管是两层：
    硬规则层（不由模型决定）——分辨率/空白/解码校验、重复图检测
                            （内容评级只写进 qc.json 供参考，不参与判定）
    决策层（本地 Agent）——读报告后决定重跑/改词/换 seed/跳过，
                          每格最多重跑次数受限，防止无限烧机时

  QC 还会跑一个漫画文字/气泡检测器（models\qc\comic-text-detector.onnx）：
    检出伪文字/气泡会记进 qc.json（text_counts / text_boxes）并打印 text=N；
    默认只报告、不判失败，要当硬门槛就加 --text-gate。
    实测：成品页 4 个中文气泡全部命中（bubble 0.951、text_bubble 精确嵌套在框内），
    无文字的普通分格零误报；草稿 p003 检出 1 处 text_free 而正式档 p003f 为 0
    —— 正好对应第三节"Turbo 档 CFG=1 负向词失效"那条。

  手动干预：
    python pipeline\agent.py status              看全局状态
    python pipeline\agent.py control --mode pause  暂停（跑完当前格停下）


五、风格训练闭环细节
----------------------------------------------------------------
  五个阶段（可单独执行，也可 run 一次跑完）：
    prep    WD 标签自动打标（不做评级筛选，目录里的素材全部参与训练）
    train   kohya anima_train_network.py（官方 Anima 支持）
    probe   每个 checkpoint 用固定探针集出图，与素材集比对打分
    deploy  最优 checkpoint 转成 ComfyUI 格式并注册

  自动评测的评分公式（三个廉价 CPU 指标，不靠感觉）：
    标签分布余弦相似度 × 0.5 + 色板 L1 距离 × 0.3 + 线密度差 × 0.2

  训练参数（默认值，可在命令行覆盖）：
    network_dim/alpha 32 · learning_rate 2e-5 · AdamW8bit
    768 分辨率 · batch 1 · 1200 步 · 每 150 步存点 · bf16 · 梯度检查点
    已开启 cache_latents / cache_text_encoder_outputs / qwen_image_vae_2d / vae_chunk_size=64
    （官方文档给的省显存开关；qwen_image_vae_2d 让 VAE 编解码约快 2 倍、峰值显存约 1/3）

  两处已按官方建议修正（原先的参数白扔了效果与速度）：
    · learning_rate 从 1e-4 降到 2e-5，步数 600 → 1200。Anima 官方微调忠告是
      「rank 32 的 LoRA 先从 2e-5 起步」；1e-4 偏高、容易过拟合，也会让推理时
      的强度必须拉到 1.0 才不漂。
    · 移除了 --discrete_flow_shift=1.0。在 timestep_sampling=sigmoid 下它被完全
      忽略（训练日志里会写明 IGNORED），只有 sigma/shift 采样才用得到它。

  训练完成后，出图任务里写：
    "style_lora": "style_<名字>.safetensors", "style_strength": 1.0
    并在提示词里带上触发词 style_<名字>

  强度取值：以实测为准，不要照抄数字。orig_student 这套在 0.8 时动态姿态
  （奔跑/远景）会漂发色，改成 1.0 才稳住（同种子 A/B 对照得出）。
  换素材重训后请用 verify_style.py 复核，再决定这个值写多少。

  用训练出的 LoRA 出图时，提示词的写法（重要）：
    触发词只承载**身份**（发型 / 发色 / 瞳色）。
    服装、配饰这类可变项必须在每一格的提示词里显式写死，否则一换场景就漂
    （实测：夕阳近景那格，黑西装漂成了围巾毛衣）。
    推荐写法（前三段来自角色设定文件，最后一段是服装）：
      style_<名字>, 1girl, solo, <镜头 / 场景 / 动作>, black blazer, white shirt, red ribbon tie, plaid skirt
    角色设定文件见 workspace\jobs\orig_student_sheet.txt

  无人值守链路里直接使用训练成果（不用手写 jobs）：
    python pipeline\agent.py plan --story <故事文件> --panels 8 --style <名字>
    规划器会自动完成三件事：把触发词放在每格提示词最前面、给每个任务挂上
    LoRA 与强度、把角色设定里的服装和道具标签逐格重复（一致性的关键）。
    名字没注册时会直接报错并提示先 deploy。

  一条命令跑完整链路（素材 -> 训练 -> 剧情 -> 出图 -> 拼版）：
    batch_run.bat --train-refs "D:\我的素材" --train-name my_char ^
        --sheet workspace\jobs\my_char_sheet.txt --auto-story --panels 4 --title 我的漫画

    复用已训练的 LoRA + 自己写的剧情：
    batch_run.bat --story workspace\jobs\story.txt --style orig_student ^
        --sheet workspace\jobs\orig_student_sheet.txt --panels 4

    只给一句前提，让本地模型自己编剧情：
    batch_run.bat --style orig_student --premise "转学第一天遇到一只猫" ^
        --sheet workspace\jobs\orig_student_sheet.txt --panels 4

  参数说明：
    --train-refs   你的素材文件夹：图会被规整进 workspace\refs\<名字>（jpg/webp/bmp
                   自动转 PNG），然后跑 prep -> train -> probe -> deploy，接着自动使用它
    --auto-story   让本地模型写剧情（第一行标题、正文每句一格）；不写就用 --story 的文件
    --premise      给自编剧情一句前提
    --sheet        角色设定文件，模型不再自编角色；用训练角色时必给
    --style        已注册的画风/角色 LoRA 名字（styles.json 里的键）
    --skip-final   只跑到草稿 + QC，不渲染正式档、不拼版
    --ref-img      角色参考图（可重复，最多用 2 张）：每一格都通过 Anima In-Context
                   节点挂上你的角色，跨格身份一致，**不需要为角色单独训 LoRA**。
                   建议一张全身 + 一张脸部特写；身份漂移时把 --ref-strength 提到 1.2-1.5。
                   依赖：models\loras\anima-incontext-character.safetensors
                        + ComfyUI\custom_nodes\comfyui-anima-incontext（本批已装）
                   注意：该 LoRA 是拿 base 模型训练的，所以带参考图的任务会自动改用
                   anima-base-v1.0 出图（除非任务里显式写了 model 字段）。
  另有一个进阶字段（命令行为主，无人值守链路暂未透传）：
    "pose_image": "<照片路径>"  用 AnimaPoseControl 从照片提取骨架做姿态控制，
                                强度用 "pose_strength"（默认 0.8），风格 "pose_style"
                                （默认 R0_thin；另有 R1_thick / R2_puppet / heatmap）。

  一张图 = 一页漫画（**现在是默认模式**：模型自己画分格，对白由代码写进气泡）：
    batch_run.bat --style orig_student --premise "转学第一天遇到一只猫" ^
        --sheet workspace\jobs\orig_student_sheet.txt --panels 2
    产出：workspace\out\pages\page_<id>.png —— 一张就是成品页，N 张就是 N 页。
    对白是**需要时才加**，不是页页都有：规划器只在"这一页真的有人说话"时才给台词，
    留白页、纯动作页、氛围页会给空字符串 —— 这是正常页，不是漏写。有台词的页，
    代码会带上 speech bubble 标签、并只对这类任务放行负向词里的气泡，于是模型画出
    一个**空白气泡**；随后 compose.py 用文字检测器找到它，涂白并把真字写进去
    （找不到气泡就在页面下三分之一兜底画一个）。没有台词的页照旧禁气泡、也不加字。
    分工的理由：模型画不对字，但画得对气泡形状；字用字体渲染，可读、可改、重写不重出图。
    要回到旧的「多格渲染 + 代码拼版」流程：加 --no-page-mode。
    实测要点：
      1) 分格是否出现，要看正式档（CFG=4/30 步）；Turbo 草稿档（CFG=1/10 步）
         经常退化成整页单场景，所以草稿只能用来确认构图和内容。
      2) 分格数量、边框粗细由模型决定，不是精确可控。要精确可控就加 --no-page-mode
         回到 compose.py 的面板拼版模式（多格渲染 + 代码拼版 + 精确格数）。
      3) 负向词仍然禁 text（伪字），但会放行 speech bubble —— 因为需要它画空泡；
         写进去的字来自字体渲染，可读、可改，重写字不必重出图。
      4) --page-panels 2|4 可以强制每页格数。实测强制 4 格：3 页里 2 页真画出 4 格
         （一页竖排四格、一页 2x2 四格），1 页只画了 2 格 —— 强制能大幅提高概率，
         但仍是概率性的，不保证。不强制时更容易退化成整页单场景。
      5) 剧情生成偶发繁体字（小模型固有偏好，指令压不住）。剧情不落在画面上，
         介意的可以手工改 story 文件，或按需接入 opencc 做确定性转换。

  注意：训练会独占显卡（约 6GB），脚本会自动先让 ComfyUI 卸载模型腾显存。
  训练期间不要同时出图。

  训练内存调参（2.9B 上必读，实测标定）：
    默认 `--workers 0`：DataLoader 不开子进程。kohya 默认开 8 个，而 --cache_latents
      把 latent 缓存在 dataset 对象里、**每个 worker 各一份**，8 个就吃掉约 8GB 内存，
      15.8GB 的机器只剩 0.1GiB，同时显存也见底，驱动无处换页 → 每步都在等。
    实测同一份素材、同一张卡（118 张 / dim32 / 1200 步）：
      768px + 8 workers   43-45 秒/步   ETA ~15 小时
      768px + 0 workers   22.3 秒/步    ETA 7 小时 20 分
      512px + 0 workers    8.09 秒/步   ETA 2 小时 38 分   ← 2.9B 的实际可用配置
      （对照）2B 768px     1.7 秒/步     ETA 34 分钟
    没有 fp8 退路：anima_train_network.py 里 --fp8_scaled 那行是被注释掉的，
      所以 5.44GiB 的权重没法减半；--blocks_to_swap 需要先把整个 DiT 加载进 CPU
      内存（5.44GiB），内存本来就紧时反而更危险。2.9B 上**分辨率是唯一的显存杠杆**。
    训练日志按 run 目录命名（train_<名字>_29b.log），2B 与 2.9B 不再混在一个文件里。


六、素材要求（重要）
----------------------------------------------------------------
  风格/角色训练的素材必须是**你拥有权利的图像**：
    自己画的、你自己用 AI 生成的、或已获授权的。
  不要使用他人的已发表作品（漫画/插画/动画截图）——这既是版权问题，
  也会让产出无法商用。

  数量建议 20-40 张，同一角色/风格，姿势、场景、构图尽量多样，
  但角色特征（发色/发型/瞳色/服装）必须保持一致。

  也可以让工作台自己造素材（自有权、零版权风险）：
    python pipeline\make_style_dataset.py --name <名字> --count 24 --sheet <设定文件>
  设定文件就是一行标签，例如：
    short silver hair, blue inner streak in bangs, red eyes, black hair clip,
    black school blazer, white shirt, red ribbon tie, plaid skirt


七、授权与内容边界
----------------------------------------------------------------
  模型授权：Anima 权重为非商用许可（circlestone-labs-non-commercial-license）。
  **生成的图像可商用，且无需署名。**
  禁止把模型权重托管到付费 API/平台。
  各 LoRA 授权独立，需逐个确认是否允许商用。

  本工作台不设内容评级闸门：WD 打标器算出的评级只写进 qc.json 作为参考信息，
  不参与通过/失败判定；训练素材同样不做评级筛选，出图提示词里也不再强制 "safe"。
  画面内容完全由调用者自己的提示词决定。


八、资源与维护
----------------------------------------------------------------
  显卡模式：默认 NORMAL_VRAM（5.5GB 权重常驻显存，最快）。
            不要随手加 --lowvram，它会加重 16GB 内存的换页压力。

  Agent 也跑在显卡上（2026-09 起）：解码 41.9 tok/s，是纯 CPU 的 9.3 倍。
            默认大脑 = Qwen3.5-9B Q4_K_M（权重 5.29GiB，ctx 6144，16 线程）
              实测 GPU：冷启动 14 秒；解码 41.9 tok/s、prefill 103 tok/s
                        （9B + 视觉投影合计约占 6.3GB 显存，全量 offload）
              实测 CPU：解码 4.8 tok/s（-ngl 0，占 0 显存）——只在显卡不够时用
            两个构建并存，启动器按空闲显存自动选（>=6500MiB 走 CUDA，否则走 CPU）：
              tools\llamacpp       CUDA 构建（生产，build 11195）
              tools\llamacpp-cpu   无 CUDA 运行时（备份，也是真正的 0 显存回退）
              注意：CUDA 构建即使 -ngl 0 也要占约 1.1GB 显存（CUDA 上下文 + 计算
              缓冲，实测）。所以"回退到 CPU"是换二进制，不是把层数调成 0——
              用 CUDA 构建跑 -ngl 0 会白占 1.1GB 还一点不快。
            只有一个大脑：4B 已删除，启动器 / fetch / install 里的引用同步清理。
            模型输出走 JSON Schema 约束解码，服务端不支持时自动降级为普通提示。
            思考模式：手动单跑时给 story / plan / judge 加 --think 即开。
              实测同一问题：关闭 8.6 秒 / 20 token；打开 66 秒 / 300 token 且被截断
              （思考没跑完、content 为空 → JSON 解析会失败）。所以代码会自动把
              max_tokens 多给 1024（思考与答案共享该预算），长度由启动器的
              --reasoning-budget 1024 封顶（GPU 上约 1.5 分钟/次，CPU 上约 4 分钟）。
              llama-server 把思考放进 reasoning_content，content 保持干净，解析不受影响。
            无人值守链路（batch_run.bat）现在是全自动的：
              拆分镜自动开思考；可自动选预设与画师；出完正式档后自动跑模型复核（judge），
              且只在"标签命中率低于 50%"的格子上才花思考的钱（并带 --vision 看图）；
              复核判"重出"的格子会自动补一张新种子的正式档，补渲染之后再发布。
              开关（都可单独关掉）：
                --no-think-plan        拆分镜不开思考（GPU 上每批省约 1.5 分钟）
                --no-judge             跳过模型复核
                --no-judge-vision      复核时不看图（快很多）
                --no-judge-think       复核不思考
                --judge-limit N        复核几格（默认 6，取命中率最低的）。
                                       实测 12.7 秒/格，全量 40 格约 9 分钟，可以放开
                --judge-think-below R  思考门槛（默认 0.5；1.0 = 每格都思考）
                --judge-reroll         默认开：裁决能触发重出（用 --no-judge-reroll 关掉）
                --judge-reroll-max N   一次运行最多重出几张（默认 4）。
                                       每页最多只给一次第二次机会，与这个数字无关
              提示词自动化（需要时才开，都只作用于本次运行）：
                --auto-preset N        让模型从"按故事检索出的短名单"里挑 ≤N 条预设。
                                       名字必须逐字命中库里的条目，所以坏挑选只会退化成
                                       "不加预设"，不会把无关内容写进提示词
                --auto-preset-adult    同时开放成人预设池。默认只从非成人池里选——
                                       基调是使用者的决定，不是模型的猜测（实测过：让模型
                                       自己判断基调会把温情校园故事误判成成人）
                --auto-artist N        让模型从"画师 + 风格关键词"菜单里挑 ≤N 位画师。
                                       前置：先跑 python pipeline\build_artist_styles.py
                                       --limit 100 建立风格索引（约 10 秒/位，可续跑）
              注意：judge 默认不再是只读的——它会排重出图并触发一次补渲染。
                    要回到旧的"只记录不行动"行为，加 --no-judge-reroll。
              前提：agent 服务必须由当前启动器启动，否则 --reasoning-budget 不生效，
                    思考会无上限直到撞 max_tokens（表现为每批莫名多等十几分钟）。
                    orchestrate 每次交接都用 tools\start_agent_headless.ps1 重启它，
                    所以跑批时这个前提自动成立；手动起服务才要注意。
            纪律：跑流水线时关掉浏览器/微信；16GB 内存是这台机器的硬上限。

  看图复核（vision judge）：Qwen3.5-9B 原生多模态，视觉投影已就位
            文件：tools\gguf\mmproj-F16.gguf（875MB），启动器检测到就自动挂载
            用法：python pipeline\agent.py judge --limit 6 --vision
            实测：12.7 秒/格（图片降到 512 之后；768 时是 23.4 秒）
            分辨率是这块最大的成本杠杆，同一张 1024² 面板实测：
              768 -> 提示词 836 token / 16.2 秒    512 -> 516 token / 6.7 秒
              384 -> 404 token / 4.5 秒            256 -> 324 token / 3.0 秒
            取 512 作拐点：判缺陷（手崩、多肢、伪字、裁脸）所需细节保留。
            价值：能指出具体、可核对的画面缺陷，而不是复读标签数字
              例：p008 判"猫趴在课桌而不是在包里"—— 核对原图，确实如此
            限制（重要）：会看错。p006 判"穿裙子而非牛仔裤"，核对原图发现
              牛仔裤在、只是旁边多了裙子残影。所以它的结论必须人工确认，
              不能直接当自动闸门用；判定记录在 audit.jsonl（vision=true 字段）。

  显存交接（一张 7.96GB 的卡，两个想独占它的东西）：
            9B + 视觉投影约占 6.3GB，Anima-2.9B 出图峰值约 7.0GB —— 两者不可能同时
            待在卡上。所以 orchestrate 在每个阶段边界做交接（pipeline\orchestrate.py）：
              需要 LLM 的阶段 → 先调 ComfyUI /free 让它交出模型，再启动 agent
              需要出图的阶段 → 停掉 agent（llama.cpp 没有卸载接口，交出显存=结束进程；
                              实测 /slots、/sleep、/models/unload 全部 404，
                              只有 --sleep-idle-seconds 的按空闲自动休眠，不可控）
            每个阶段用 needs="llm" / "gpu" 声明自己要什么。写在阶段上、而不是在阶段
            之间手插交接调用，是为了以后增删阶段时两边不会悄悄错开。
            实测一次完整交接（_stage\test_handoff.py）：
              ComfyUI 持模型时 1257MiB 可用 → /free 后 6919MiB
              agent 冷启动 14 秒（ngl=99，视觉在位）
              停 agent 2.8 秒 → 6919MiB 全部还给渲染
              ComfyUI 自己重新加载模型并成功出 2.9B 图（67.3 秒）——不需要重启
            代价：一次运行约 5-6 次交接，合计约 1.5-2 分钟；换来 LLM 快 9.3 倍。
            启动器无法被抓取输出：它拉起的服务会继承句柄，用 capture_output 读它会
            一直阻塞到该服务退出（实测：capture 卡死，Popen 不 capture 0.01 秒返回）。
            所以它把决定写进 workspace\logs\agent-launch.log，运行日志再读回来。

  缓存位置（都在站内，删目录即清）：
    env\hf  env\torch  env\pip.ini  env\rtmlib_cache
    唯一例外：%USERPROFILE%\.cache\rtmlib 是目录联接，指向 env\rtmlib_cache

  启动器都在根目录，全部为纯 ASCII（避免 cmd 中文解析错位）：
    start_comfyui.bat  图形界面出图
    start_agent.bat    只启动本地 Agent LLM
    batch_run.bat      无人值守全流程
  tools\ 下有对应的 headless 版本（不弹窗、写日志）。

  维护建议：跑批前关闭浏览器与微信（16GB 内存是真正的瓶颈）。


九、常用命令速查
----------------------------------------------------------------
  # 出图（草稿 → 正式 → 拼版）
  python pipeline\agent.py plan --story workspace\jobs\story.txt --panels 8 --draft
  python pipeline\runner.py
  python pipeline\qc.py
  python pipeline\agent.py review
  python pipeline\agent.py finalize
  python pipeline\runner.py
  python pipeline\compose.py --cols 2 --rows 4 --panels p001f,p002f --title 标题

  # 风格训练
  python pipeline\style_train.py prep   --name my_style
  python pipeline\style_train.py train  --name my_style --steps 600
  python pipeline\style_train.py probe  --name my_style
  python pipeline\style_train.py deploy --name my_style

  # 工具
  python pipeline\wd_tagger.py --image <图>            看图的内容评级与标签
  python pipeline\wd_tagger.py --dir <目录> --write-txt 批量打标
  python pipeline\check_weights.py <safetensors...>     校验权重文件完整性

================================================================