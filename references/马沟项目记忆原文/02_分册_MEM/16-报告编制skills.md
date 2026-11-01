# MEM/16 三个报告编制 skills（2026-10-09 云端）

> 本册**不自动加载**，按 `MEMORY.md` §1 路由表显式 Read。
> 收录：三个 skills 的名称、存放位置、内容构成、用法，以及记忆变了以后怎么同步。skills 是从本记忆包和马沟最终版**蒸馏**出来的，
> **口径冲突时以本记忆包和各报告包为准**，再回头改 skill。

## 三个 skills
| skill（Notion 页面标题） | 文件夹名（SKILL.md 的 name） | 管什么 | 主要依据 |
|---|---|---|---|
| 预评价报告编制 | `occupational-hazard-pre-assessment` | 建设项目职业病危害预评价报告：资料清单、11 章骨架、各章写法、四档判定、生成器与排版、自检 | GBZ/T 196—2025、GBZ/T 277—2016 |
| 设计专篇编制 | `occupational-disease-protection-design` | 职业病防护设施设计专篇：主报告＋资料性附件册、预评价措施逐条采纳／调整、投资、生成器 | WS/T 882—2013（ZW-JB-2014-002 只作参考） |
| 控评报告编制 | `occupational-hazard-control-evaluation` | 职业病危害控制效果评价报告：11 章骨架、检测与评价、承接预评价和设计专篇、补丁型生成器 | GBZ/T 197—2007、GBZ/T 277—2016（ZW-JB-2014-003 只作参考） |

- **存放**：Notion「马沟加气站」页下三个子页，已设为 Skill（设置时返回“已启用”）；每页附一个 zip（`<中文名>_skill.zip`，标准 Agent Skills 格式）。
  - 预评价报告编制：https://app.notion.com/p/539b7c3728ac4d4e845f061f8edfbab7 （zip 143 个文件，3,584,777 B，sha256 `85a436baa4b954e5…`）
  - 设计专篇编制：https://app.notion.com/p/31553171ef944b0e942aca77847fe0ac （zip 111 个文件，7,221,688 B，`5c041a5d242f9cb9…`）
  - 控评报告编制：https://app.notion.com/p/1e0cc0001b264f2db8ea4b86fa68c47f （zip 86 个文件，1,897,588 B，`83d9d8f4e70a9f08…`）
  - 10-09 调 `notion_update_enabled_skills` 报参数错误（addSkillUrls 不被接受），未单独再启用；若在 Notion AI 的“/”里找不到，到页面右上角把 Skill 打开即可。
- **zip 结构**：`<name>/SKILL.md`（YAML 头 `name`、`description`＋正文流程）＋`references/`（口径红线、章节写法、判例卡、三报告衔接、依据清单）
  ＋`scripts/`（可复用脚本：自检、目录页码校验、补丁型生成器模板、渲染）＋`examples/马沟/`（最终版成品与说明）。
- 三个 skill 共用一份「三报告衔接」（时序前不引后、共用事实、核查表）和通用判例（PL-GEN），各自另带本类判例（PL-PRE／PL-DES／PL-CTL）。

## 怎么用
- **Notion AI**：在对话框输入「/」选 skill（或直接说「按预评价报告编制 skill 做……」）；skill 页正文就是指令，需要脚本和参考文件时，
  Notion AI 用 `notion_view_file_url` 取页内 zip 到自己的电脑解压使用。
- **本机 Claude Code／Codex 等**：Notion 页面「•••」→「Download to local agents」；或直接把 zip 解压到本机 skills 目录（如 `~/.claude/skills/`、`~/.codex/skills/`）。
- 新项目：先按 SKILL.md 的「开工前收集资料」清单要资料，缺的列「待核」，不臆造；马沟成品只作范例，**不得把马沟的事实带进新项目**（`PL-PRE-0008`）。

## 记忆变了怎么同步（维护规矩）
- 改了本记忆包的口径（`MEM/01`、`08`、`10`、`11`、`12`、`13`、`14`）或新增判例 → 同步改对应 skill 的 `references/`，必要时改 SKILL.md 的红线和自检清单，再重打 zip、替换 Notion 页内附件。
- 改了生成器（预评价同步包、设计包脚本、控评 `04_生成器/`）→ 同步更新 skill 的 `scripts/` 或在 SKILL.md 里写明以包内为准。
- skill 页正文和 zip 里的 SKILL.md 保持同一内容（页面是给 Notion AI 读的，zip 是给本机 agent 的）；改一处必须改另一处。
- 判例卡的 `已蒸馏到` 字段：本轮未逐张标注，回本机跑 `build_moc.py` 时再统一处理。
- **云端组装脚本**（不随包，记在这里备查）：`/data/fin/skills/build_pre.py`、`build_des.py`、`build_ctl.py`（公共 `build_common.py`、`cards.py`；源文件在 `skills/src/`）。判例速查由卡片头部自动生成；三个 skill 各带 PL-GEN 54 张＋本类判例，另带「三报告衔接」引用的 PL-DES-0001、PL-CTL-0002（控评另带 PL-PRE-0001）。
- **依据层级**：三个 skill 都按 `PL-GEN-0033` 把 ZW-JB 编制要求写成“只作参考、不作符合性依据”；马沟设计专篇、控评正文的依据清单已列入 ZW-JB-2014-002／003，属交付稿，删不删先问用户（`PL-GEN-0033` 例外②）。
- **控评导则核对**：GBZ/T 197—2007 第10章条款结构（10.1～10.11）、第4.2.3条、附录A、附录C 已于 10-09 按公开网页转录本核对，写成控评 skill 的 `references/依据/GBZ-T197-2007_条款要点.md`（不是全文）。
