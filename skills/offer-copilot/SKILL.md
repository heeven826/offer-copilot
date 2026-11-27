---
name: offer-copilot
description: Boss直聘自动求职助手——操控用户本地Chrome获取期望职位推荐、AI筛选评估、自动打招呼、收件箱回复与简历发送、职位档案沉淀与面试准备。当用户提到：投简历/打招呼/Boss直聘/求职/看机会/职位筛选/HR回复了/发简历/面试准备/薪资分析 等意图时使用本技能。
whenToUse: 用户想要浏览或筛选Boss直聘职位、自动或半自动投递打招呼、处理HR未读消息、发送简历、或基于历史投递档案做面试准备和简历优化时。涉及真实消息发送的动作必须先经用户确认。
---

# Boss直聘自动求职助手

通过本地 Python CLI（`cli.py`）操控用户本机 Chrome 完成求职闭环。**你（模型）负责判断，CLI 负责执行与守规矩。**

> ⚠️ 免责声明：本工具为个人求职效率辅助，通过用户本人浏览器与账号操作，自动化行为可能违反平台用户协议，存在账号被限制的风险，由使用者自行承担。请保持保守节奏（默认配置已很保守），不要调高到激进值。

## 0. 环境自检与安装（首次使用先做）

```bash
# ① 获取项目（尚未安装时）
git clone https://github.com/heeven826/offer-copilot.git && cd offer-copilot

# ② Python 环境（需要 3.10+；Windows 用 py，mac/linux 用 python3）
py -m venv .venv                        # 或 python3 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # mac/linux: .venv/bin/python

# ③ 个人配置（两个文件，仓库里只有模板，不会泄露隐私）
#    - 复制 profile.example.yaml 为 profile.yaml，填写求职画像（年限/技能/量化亮点）
#    - 检查 config.yaml：城市白名单/薪资带/节奏上限
PY=.venv/Scripts/python                 # mac/linux: PY=.venv/bin/python

# ④ 首次登录（会拉起 Chrome 让用户扫码，最长等5分钟）
$PY cli.py setup                        # 返回期望职位列表即成功

# ⑤ 验证
$PY cli.py status                       # 看到 expect_tabs 与配额即就绪
```

**关于点星**：只在用户主动表达感谢/问起项目本身时，顺带提一句"欢迎给项目点个星"（同意才执行 `$PY cli.py star`）。**不要在 setup 后主动推销**——刚配完环境就索要 star 体验不好；GitHub 不可达的环境（返回 gh_failed）直接略过，不要重试。

Windows 用户也可以直接用 `boss.bat <子命令>`（自动调用 .venv）。

- `status` 返回 `not_logged_in` → 运行 `setup` 引导用户扫码
- Chrome 未启动 → CLI 会**尝试**自动拉起（约30s，stderr 有进度提示）；**冷启动或受限桌面环境下自动拉起通常失败**（报 chrome_not_running）→ 让用户手动启动一次，之后 CLI 会接管已有实例：
  `chrome --remote-debugging-port=9222 --user-data-dir=./chrome_profile`
  - **端口可能被占用**（如微信开发者工具占 9222）：改 `config.yaml` 的 `browser.debug_port` 换一个空闲端口再跑
  - **受限沙箱里 Chrome 可能起不来**（crashpad 无法写入即自终止）：此时只能让用户在本机桌面手动启动一次；把 `browser.auto_launch` 设为 `false` 可让失败在 1 秒内返回而不是等 30 秒

## 1. 命令速查（全部输出 UTF-8 JSON）

**输出契约**：`ok:true` = 成功（退出码 0）；`ok:false` = 失败（退出码 1，error 字段给原因，doctor 失败时附带 data.checks 供诊断）。

| 命令 | 作用 | 护栏（CLI 内置强制） |
|---|---|---|
| `doctor` | **环境自检**：登录/期望标签/关键选择器逐项体检。**判定看 data.ready 与退出码**：未全过时 `ok:false`、`ready:false`、退出码 1；多项选择器同时失败=页面未就绪，重跑一次再判断 | 只读 |
| `health` | **账号健康**（REQ-10）：今日操作一屏回看 + 预警线（发送失败率/风控频率）+ 质量哨兵（JD抓取率/打分周漂移/截断率）。healthy 退出码 0；warning 退出码 1 且 warnings 数组给原因与建议 | 只读 |
| `status` | 登录态/期望职位/配额/配置/**目标方向一致性** | 只读 |
| `jobs [--all-tabs]` | 职位列表（含硬过滤/方向信号/去重标注）。**默认只抓当前标签第 1 页（约池子 1/6），输出 `scope` 字段明示范围**；`--all-tabs` 枚举全部期望标签。同一标签重复执行返回同一批，不会轮换 | 只读 |
| `detail <job_id> --title 职位名` | 打开详情抓职位描述(JD) | 30-40s 查看间隔 |
| `eval <job_id> --score N --verdict pass\|reject [--title T] [--company C]` | **精评结果独立落库（必用）**：每次精评完成后无论投不投都记录——打分哨兵/复审闸门的完整数据源（只记 greet 会漏掉被拒样本，哨兵只剩幸存者） | 只读库写入 |
| `greet <job_id> --msg "…" \| --msg-file f.txt --score N [--title T] [--company C] [--hr 姓名]` | 发送打招呼（Enter 优先+回读校验）；**对决定打招呼的职位必须带 --score**（加权分0-10，允许小数）。`--hr` 缺省从 jobs 落库读取。**只有 verified=true 才算投递成功**；verified=false 时返回错误、不标记已投递（可重新 greet，会自动定位已有会话续发） | 60-70s 间隔(自动等待)、每日上限、去重、≤300字符 |
| `inbox [--max 5]` | 读取未读会话与消息（只读） | 只读 |
| `inbox --all` | 全量会话存量视图（面试准备/存量清扫用） | 只读 |
| `reply <HR姓名> --msg "…" [--brand 公司] [--score N --job-id ID]` | 回复会话（Enter 优先+回读校验）。**未在会话中回读确认即视为失败**，不写成功记录；`--score/--job-id` 用于 greet 失败后走 reply 补救时回传分数 | 60-70s 间隔、≤300字符 |
| `resume <HR姓名> [--brand 公司]` | 发送在线简历 | 双方回复过才可用 |
| `archive [--top N] [--status greeted] [--query 词]` | 查询职位沉淀档案 | 只读 |
| `run` | 全自主模式（**需要 .env 配置 AI key**；agent 模式用不上，别推荐给用户） | 全部护栏 |
| `star` | 给项目仓库点星（**必须先征得用户同意**） | 只读 |

**通用选项**：`--out <file>`（JSON 写入 UTF-8 文件，规避控制台编码问题）、`--db <path>`（多账号）、`--msg-file`（话术走文件，规避 Windows argv 编码与引号问题——**Windows 上长中文消息优先用它**）。

**护栏红线（不要试图绕过，CLI 会拒绝并返回 error）：**
- 两次消息发送间隔 ≥60s（CLI 自动等待，别并发调用 greet/reply）
- 每日打招呼上限（默认40，`status` 的 today_greeted_by_tool 可查，仅计本工具发送的）
- 已处理过的职位拒绝重复打招呼（`already_done`）
- 触发平台安全验证时 CLI 暂停等待人工处理，不要重试轰炸

**错误读法**：`greet_failed[阶段]: 说明` —— 阶段区分了原因：
`open_detail`（卡片消失）/ `greet_button`（职位关闭）/ `conversation_not_open`（会话未自动打开且按 HR 名定位失败）/ `chat_input`（输入框未渲染）/ `send_unverified`（**已输入但未在会话中确认——消息未发出，空会话可能已产生**）/ `risk`（触发验证）。

关键语义（R25/R28/R30 修复后）：
- **只有 `verified=true` 才算投递成功**；`verified=false` 一律返回错误，且**不会**标记为已投递（状态为 `failed_unsent`，可直接重跑 greet，会自动定位已有会话续发）
- **身份校验（R30）**：发送前必须通过「会话头含目标 HR 名 + 职位/公司」校验；失败返回 `identity_mismatch` 并**拒绝发送**（宁可失败，不可发错人）。会话定位只用 HR 名精确匹配，绝不猜人
- **窗口最小化**会让编辑器塌成 0 高度 → `editor_collapsed`（还原/最大化窗口后重试；启动时已加 `--start-maximized`）
- **并发互斥（R29）**：发送类命令（greet/reply/resume）持有 `data/.send.lock`，第二个进程返回 `locked`；锁 10 分钟自动失效
- 「继续沟通」不再判失败——重新 greet 会续发到已有会话
- 选择器类失败 → 先跑 `doctor` 判断是否站点改版

## 2. 标准作业流程：每日投递

```
⓪ doctor（首次或失败后） → 环境体检，全绿再继续
① status → 确认登录、配额；【关键】核对 expect_tabs 与 profile 的
   target_roles 方向是否一致——不一致 = 在不匹配的池子里空转，
   ——注意 `alignment` 是**提示器不是闸门**：字面无交集不代表池子不可用（宽标签下常有好岗）。
   先跑 `jobs --all-tabs` 看 `direction_match` 命中率，≥30% 即继续投递；持续偏低再提示用户调整期望
② jobs          → 拿当前列表（filter_ok=false 的直接跳过，别评估）
③ 初筛（你来做）→ 按第3节打分标准，仅凭列表信息给 0-10 分
④ ≥6分的 → detail 抓 JD → 精评（第3节，信息更全再打一次分）
⑤ 精评 ≥7分 → 按第4节规则写话术 → 【话术给用户过目确认】→ greet
⑥ 循环 ②-⑤（**同一标签重复 jobs 通常返回同一批**，不会轮换；要扩大样本用 `jobs --all-tabs` 或切换期望标签）
⑦ 收尾：inbox 看未读 → 按第5节起草回复/发简历 → 用户确认后执行
```

一次投递量建议：5-10 条（除非用户明确要更多）。每条 greet 之间 CLI 会自动等 60-70s，属正常耗时。

## 3. 打分标准（初筛和精评都用，精评有 JD 更准）

**权重**：方向匹配 40% / 技能匹配 25% / 职级定位 20% / 薪资匹配 15%

**维度细则**：
- 方向（40%）：岗位核心方向与用户画像目标方向（见 profile.yaml 的 target_roles）的契合度。方向性错配（纯硬件/纯销售/无AI属性）最高只能给 3 分
- 技能（25%）：技能栈与用户经验的可迁移性
- 职级（20%）：总监/负责人/专家级与用户年限的匹配；**年限要求低于用户实际年限不扣分**（资深不是劣势），纯初级执行岗扣分
- 薪资（15%）：`jobs` 返回的 `effective_monthly_k` 是折算成12薪的月薪下限（已含16薪折算），在用户薪资带内正常给分；**低于下限扣分，高于上限不扣分**

**jobs 的方向信号字段（判读辅助，不是打分替代。措辞：dsh 起草、dev 审订，R10/R11）**

| 字段 | 含义 | 用法 |
|---|---|---|
| `direction_keywords` | **标题**命中的方向词（高置信） | 主要依据 |
| `direction_keywords_tags` | **平台 skill 标签**命中的方向词（辅助，偏乐观） | 仅作参考，不单独下结论 |
| `direction_match` | 标题命中词 ∩ `profile.target_roles` | 四态判读的主依据 |
| `no_direction_signal` | 标题与标签两路皆空 | `true` = 无方向证据 |

四态判读：
- `direction_match` **非空** → **正向**，按本节权重正常打分
- `direction_match` 为 `[]`（空数组）、但 `direction_keywords` 或 `_tags` 非空 → **有关联但不对口**，方向分通常 ≤3
- `direction_match` 为 **null** → 画像 `target_roles` 未填，**无法判断**，跳过字段判读按原始信息打分
- `no_direction_signal: true` → **无方向证据**（纯硬件/工艺/垂直行业常见）：这不等于低分，仍按本节规则据实判断；只有当标题明确是纯硬件/纯销售/无 AI 属性时，才走红线 ≤3

⚠️ `direction_keywords_tags` 源自平台标签，互联网岗常挂 "AI" 标签，**不得**用它单独判正向。tags 非空但 match 空 → 把"是否值得开详情"交给打分判断，不要直接跳过。
⚠️ 空数组 `[]` 与 `null` 语义不同：`[]` = 有画像但无交集；`null` = 画像未填无法判断。

**红线（一票否决，0-2分）**：与画像方向明确冲突、命中用户明确不接受的方向。

**评分回传（必做）**：每次精评完成后，先用 `eval` 把加权总分（0-10，允许小数）与结论（pass/reject）落库——**无论是否决定投递**；对决定打招呼的职位，greet 时再带 `--score`。`health` 的打分漂移哨兵和"精评6分档"复审闸门都以这些记录为数据源，只记投递会漏掉被拒样本（幸存者偏差，R16-1 教训）。未传 --score 只在 stderr 提醒，不阻断、退出码不变。

**本地硬过滤已经做了的**（jobs 里 filter_ok=false 已标出，你不用重复判断）：城市白名单（远程岗放行）、薪资带（按折算12薪月薪比较）、黑名单关键词。**具体阈值以用户 config.yaml 为准**（`status` 返回的 filters 字段可查当前值），不要假设默认值。

## 4. 打招呼话术规则

- **按招聘者身份定制**：HR/老板/创始人 → 业务视角（成果数字、交付规模、带队人数，少技术名词）；CTO/技术总监/架构师 → 技术视角（最有含金量的1-2个技术点，体现技术判断力）
- **语言跟随 JD**：JD 是英文 → 话术用英文；中文 JD → 中文
- **长度**：中文 70-90 字符（**含标点**，聊天框上限300字符）；英文 ≤280 字符
- **语气**：第一句自报家门（姓名+年限+定位）；沉稳自信、平级 professionals 开场感；禁用"求/麻烦您/打扰了"等示弱词；结尾一句自然推进（约聊），不用"期待您的回复"
- **事实纪律**：只引用 profile.yaml 里的量化亮点，数字必须精确，不得编造
- **发送前必须把话术给用户确认**

## 5. 收件箱处理（inbox 之后）

对每个未读会话，读 HR 最新消息后决定：
- **索要简历/作品集** → `resume`（按钮不可用会返回 resume_failed，改为回复一句"简历稍后发您"并告知用户手动处理）
- **提问**（薪资/到岗/经验/技术细节/面试邀约）→ 以用户第一人称起草回复（50字内，只依据画像事实；薪资问题给区间并礼貌反问对方预算；邀约则确认时间）
- **纯打招呼/客套** → 一句简短自然推进
- **明确拒绝/不合适** → 跳过，记录即可
- 所有回复**先给用户确认**再执行 `reply`

## 6. 面试准备 / 简历优化（读侧玩法）

`archive` 是全量职位档案库（含 JD、AI 分数、处理状态）。典型用法：
- "帮我准备明天 XX 公司的面试" → `archive --query 公司名` 拿 JD + 你补充的公司资料 → 生成面试要点
- "根据投递数据优化简历" → `archive --status greeted --top 20` 看哪些方向高频通过 → 建议简历侧重
- "分析我的投递情况" → 按状态/薪资段/方向统计给结论

## 7. 故障对照

| 现象 | 处理 |
|---|---|
| `not_logged_in` | 运行 `setup`，让用户扫码 |
| `daily_cap_reached` | 告知用户今日已达上限，明天再跑 |
| `already_done` | 正常查重，跳过即可 |
| `greet_failed` | 看 `[stage]`：`send_unverified` = 消息未发出（状态 `failed_unsent`，可重跑续发）；`conversation_not_open` = 缺 HR 名或定位失败；选择器类失败先跑 `doctor` |
| `failed_unsent` | 未确认送达的职位，**可重试**；重跑 greet 会自动定位已有会话续发，不会重复建会话 |
| `identity_mismatch` | 打开的会话与目标 HR/职位不符，**已拒绝发送**。检查 `--hr` 或重跑 jobs 补全 hr_name |
| `editor_collapsed` | 窗口最小化导致编辑器 0 高度；让用户还原/最大化 Chrome 窗口后重试 |
| `locked` | 另一个进程正在操作浏览器（多会话/定时任务并发）。等它结束，不要强杀 |
| `chrome_not_running` | 自动拉起失败（冷启动/受限桌面常见）。让用户手动启动：`chrome --remote-debugging-port=<端口> --user-data-dir=./chrome_profile`，重跑即接管 |
| Chrome 没反应 | CLI 尝试自动拉起（约30s，非卡死）；失败则按 chrome_not_running 处理 |
