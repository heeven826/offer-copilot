# offer-copilot —— Boss直聘 AI 求职助手

操控你本机的 Chrome（保留真实浏览器指纹与登录态），获取期望职位推荐 → AI 评估匹配度 → 自动打招呼 → 收件箱回复/发简历 → 全程职位档案沉淀（简历优化/面试准备素材）。

**两种用法：**
1. **AI Agent 技能（推荐）**：装到 DeepSeek Harness / Claude Code 等 harness，用自然语言指挥（"帮我看看今天的职位，合适的投几个"）
2. **独立 CLI**：命令行直接跑（含全自主模式，无人值守）

> ⚠️ **免责声明**：本工具为个人求职效率辅助，通过用户本人浏览器与账号操作。自动化行为可能违反平台用户协议，存在账号被限制的风险，由使用者自行承担。默认节奏配置已很保守（消息间隔 ≥60s、每日上限 40），请勿调到激进值。

## 🚀 一句话启动

把下面这句**复制给你的 AI Agent**（DeepSeek Harness / Claude Code / WorkBuddy / Codex / Cursor 等任何能执行 shell 的助手），它会自动安装技能、引导配置并开始投递：

**中文：**
```
帮我安装并启动 offer-copilot 求职助手：先运行 npx -y skills add heeven826/offer-copilot 安装技能，然后严格按技能说明（skill/offer-copilot/SKILL.md，若本会话未识别新技能就直接读仓库里的这个文件）完成环境自检与配置，最后问我今天想投递几个职位，再按技能里的流程开始。
```

**English:**
```
Install and start offer-copilot (a job-hunting assistant): first run `npx -y skills add heeven826/offer-copilot`, then follow the skill instructions strictly (skill/offer-copilot/SKILL.md — if the new skill isn't loaded in this session, read that file from the repo directly) to set up the environment, then ask me how many jobs to apply today before starting.
```

为什么这句话有效：agent 会自己装技能 → 技能里有完整的安装引导、打分标准、话术规则和护栏说明 → 真实消息发送前一定会来找你确认。

## 安装

```bash
git clone https://github.com/heeven826/offer-copilot.git && cd offer-copilot

# Python 3.10+
py -m venv .venv                        # mac/linux: python3 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt

# 个人配置
#  1) 复制 profile.example.yaml → profile.yaml，填写你的求职画像
#  2) 复制 config.example.yaml → config.yaml，调整城市白名单/薪资带/节奏
#  3) 检查 .env.example → .env（如需内置 AI 评估的自主模式 run；agent 技能用法可不填）

# 环境自检（登录/期望标签/关键选择器逐项体检）
.venv/Scripts/python cli.py doctor

# 首次登录（拉起 Chrome 扫码）
.venv/Scripts/python cli.py setup
```

## 用法一：AI Agent 技能

本仓库是一个标准的 [Agent Skill](https://github.com/vercel-labs/skills)（`skill/offer-copilot/SKILL.md`）：

```bash
npx skills add heeven826/offer-copilot    # 安装到你的 harness
```

然后在 DeepSeek Harness / Claude Code 等支持技能的 agent 里直接说：
- "帮我看看今天的职位，合适的投 3 个"（agent 会按技能里的打分标准筛选，话术发前给你确认）
- "有 HR 回复我吗？" / "帮我准备明天 XX 公司的面试"

## 用法二：独立 CLI

```bash
PY=.venv/Scripts/python     # mac/linux: PY=.venv/bin/python

$PY cli.py status           # 登录态/期望职位/今日配额
$PY cli.py jobs             # 当前列表（含硬过滤标注）
$PY cli.py detail <职位ID>  # 抓职位描述
$PY cli.py greet <职位ID> --msg "话术"
$PY cli.py inbox            # 未读消息
$PY cli.py archive --top 20 # 职位档案查询

# 全自主模式（内置两阶段AI评估：粗筛→详情精评→自动打招呼→收件箱跟进）
$PY cli.py run --dry-run --max-jobs 10   # 演练
$PY cli.py run --max-greets 5            # 正式
```

Windows 可双击 `start.bat`（交互菜单）或 `boss.bat <子命令>`。

## 处理流程

```
打开期望职位页(/web/geek/jobs) → 监听接口拿明文职位数据
  → 硬过滤（城市白名单/薪资带[折算12薪]/黑名单，零成本）
  → AI 粗筛（仅列表信息，≥6分）→ 打开详情抓 JD
  → AI 精评（含完整JD，≥7分）→ 分对象生成话术（JD英文则英文话术）
  → 打招呼（60-70s 随机间隔、每日上限、去重、风控暂停）
  → 收件箱：读未读 → AI 起草回复/发简历（经用户确认）
  → 全量职位+JD 沉淀 SQLite 档案库，可导出 CSV/MD
```

## 架构

```
cli.py                原子 CLI（agent 友好，JSON 输出，护栏内置）
main.py               全自主模式入口
skill/offer-copilot/  Agent Skill（SKILL.md：打分标准/话术规则/SOP）
boss_browser/         browser(接管Chrome) · expect_page(列表采集/翻页)
                      actions(打招呼/详情) · inbox(收件箱) · risk(风控暂停)
ai/                   judge(两阶段评估) · greeting(分对象/中英文话术) · reply
storage/              SQLite（档案库/投递记录/跨进程护栏状态）
filters.py            硬过滤（城市/薪资带/黑名单）
safety.py             节奏与每日上限
```

## 配置要点

- `config.yaml`：`filter.allowed_cities`（城市白名单，远程岗放行）、`filter.salary_band_k`（折算 12 薪的月薪下限区间）、`rate.*`（节奏/上限——**别调激进**）
- `profile.yaml`：画像的量化亮点越实，AI 筛选与话术越准
- 职位期望在 Boss直聘 App 的「期望管理」里维护（工具自动读取你的期望标签）

## License

MIT（见 [LICENSE](LICENSE)）
