# Repository Guidelines

用中文回复用户。本仓约束分置于本文件（常驻）、`CONTEXT.md`（术语）与各 skill 的 `SKILL.md`（流程），各有唯一职责、按需披露。

系统分两层：采集（watch-pipeline：CI 只抓公开数据进仓库）→ 计算与写入（axonhub-admin：四步流水线——渠道同步过滤、模型卡更新、非 Claude 关联、Claude 映射，每步一个离线脚本计算并呈报，无中间产物文件，用户确认后受控写入 AxonHub）。计算阶段纯离线、不联网、不持凭据；AxonHub 写入只在交互会话执行，CI 永不写。

## 何时读什么

- 术语、映射判断、门禁分级 → [`CONTEXT.md`](CONTEXT.md)
- 采集流程与 workflow 安全 → `.agents/skills/watch-pipeline/SKILL.md`
- 四步流程（渠道同步/模型卡/关联路由/Claude 映射）、凭据纪律、执行循环、事件响应 → `.agents/skills/axonhub-admin/SKILL.md`（按任务导航到其 `references/`）
- 改变拥有或写入边界 → 先改对应 skill 的 `SKILL.md` 与 `CONTEXT.md`，再改本文

## 命令

```bash
python3 .agents/skills/axonhub-admin/scripts/channel_sync.py        # ① blocklist 物化 + 同步正则
python3 .agents/skills/axonhub-admin/scripts/model_card_update.py  # ② 模型卡目标态（--id 单模型写时 payload）
python3 .agents/skills/axonhub-admin/scripts/channel_assoc.py      # ③ 关联回退链
python3 .agents/skills/axonhub-admin/scripts/claude_map.py         # ④ Claude 映射建议
python3 -m pytest -q
```

四个任务脚本均纯离线（仓库根运行）、stdout 呈报计算结果，不写 AxonHub、不落中间产物文件；`channel_sync.py` 的 blocklist 物化是唯一落盘写。

采集脚本位于 `.agents/skills/watch-pipeline/scripts/`，由 `watch-pipeline` skill 维护并经 watch-pipeline.yml（每天）执行；本地运行 watch_* 仅作调试，产物仍归对应渠道。go 与 goat 两个采集 job 都写 `data/models_extra.json` 的各自节，必须串行（CI 已按 go → goat 排序）。脚本失败会在 `.agents/skills/watch-pipeline/reference/<channel>/last-error.json` 留痕，修复流程见 `.agents/skills/watch-pipeline/SKILL.md`。skill 校验见各自 `SKILL.md`。

## 约束

- Python 3.12+，只用标准库；公开函数带类型注解。
- `data/models_extra.json` 渠道节内记录的契约字段由对应采集脚本独占写入，按字段级合并（新 id 插入、消失 id 连记录删除，契约外字段保留）；免费语义即 `cost` 零价：采集器把渠道免费措辞转写为零价（静态节人工声明），计算层只读价格判定，无旗标或 id 后缀启发式；顶层 `aliases` 人工维护；顶层 `blocklist`（`{id, reason}`，唯一排除源）中 `speed:`/`lowscore:` 两类由 channel-sync 脚本（`channel_sync.py`）物化重建，`manual`/`tier`/`retired` 等人工类不归采集器也不被覆盖，删除重插不影响它们；例外：`ant`、`sensenova` 为人工维护静态节（ADR 0013），任何 watcher 不得触碰。模型卡只来自 `data/all_models.json`，渠道 `cost` 逐字段优先；仅 Arena 允许使用文档化的 fallback 链。
- 渠道自带的 `claude-*` 模型不采集：Claude 由 AxonHub 自建全局模型 + Arena 映射供给（ADR 0012）；GPT 按名透传，不参与映射。
- 外部抓取只发生在 watch-pipeline；axonhub-admin 的计算脚本只消费仓库内快照做离线计算（不联网、不持凭据）；AxonHub 写入只在交互会话执行，CI 永不写 AxonHub、也不持有其凭据。
- CI workflow 安全：第三方 Actions 固定到已审核版本或 commit SHA、最小权限；不把抓取内容、commit message 或分支名拼进 shell 命令；CI 不执行测试，`pytest` 由维护者本地运行。
- `data/arena.json` 的 `manual: true` 记录与 `models_extra.json` 的 `aliases`、`blocklist` 是手工维护数据，采集/重生成必须保留（脚本已保证，不得绕过脚本直写文件）。
- 请求模型清单是 `claude_map.py` 的 `REQUESTS` 字典（claude-* id → 人工备注），加删条目即增减请求模型；映射建议每次运行重算、stdout 呈报，不落文件。
- 保留无关的脏工作区改动。
- 凭据与写入门禁以 `.agents/skills/axonhub-admin/SKILL.md` 为准（token 纪律、确认门、事件响应）；门禁分级以 `CONTEXT.md` 为准。

## 项目元数据

- Issues: `docs/agents/issue-tracker.md`
- Triage labels: `docs/agents/triage-labels.md`
- Domain documentation: `docs/agents/domain.md`
