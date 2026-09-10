# Repository Guidelines

用中文回复用户。本仓约束分置于本文件（常驻）、`CONTEXT.md`（术语）与各 skill 的 `SKILL.md`（流程），各有唯一职责、按需披露。

系统分两层：采集（watch-pipeline：CI 只抓公开数据进仓库）→ 规划与写入（axonhub-admin：离线重算去重、补卡、Claude 映射建议与 channel/model 双 plan，用户确认后受控写入 AxonHub）。规划阶段纯离线、不联网、不持凭据；AxonHub 写入只在交互会话执行，CI 永不写。

## 何时读什么

- 术语、映射判断、门禁分级 → [`CONTEXT.md`](CONTEXT.md)
- 采集流程与 workflow 安全 → `.agents/skills/watch-pipeline/SKILL.md`
- 规划/写入流程、凭据纪律、执行循环、事件响应 → `.agents/skills/axonhub-admin/SKILL.md`（按任务导航到其 `references/`）
- 改变拥有或写入边界 → 先改对应 skill 的 `SKILL.md` 与 `CONTEXT.md`，再改本文

## 命令

```bash
python3 .agents/skills/axonhub-admin/scripts/models_mapping.py --csv models.csv --fail-on-errors
python3 -m pytest -q
```

规划默认写出 `models.csv` 与 `data/model-plan.json`（生成物，整体重算，不手工编辑）；单模型卡终态用 `.agents/skills/axonhub-admin/scripts/assemble_card.py --id <modelID>` 在写入前组装。

采集脚本位于 `.agents/skills/watch-pipeline/scripts/`，由 `watch-pipeline` skill 维护并经 watch-pipeline.yml（每天）执行；本地运行 watch_* 仅作调试（例外：channel-sync 流程缺 Arena 分数时授权主动调用 watch_arena.py 刷新快照），产物仍归对应渠道。go 与 goat 两个采集 job 都写 `data/models_extra.json` 的各自节，必须串行（CI 已按 go → goat 排序）。脚本失败会在 `.agents/skills/watch-pipeline/reference/<channel>/last-error.json` 留痕，修复流程见 `.agents/skills/watch-pipeline/SKILL.md`。skill 校验见各自 `SKILL.md`。

## 约束

- Python 3.12+，只用标准库；公开函数带类型注解。
- `data/models_extra.json` 渠道节内记录的契约字段由对应采集脚本独占写入，按字段级合并（新 id 插入、消失 id 连记录删除，契约外字段保留）；记录上契约外的字段仅 `free` 旗标可人工维护（`-free` 后缀为规划器派生默认，旗标可覆盖）；顶层 `aliases` 与 `blocklist`（人工模型决策，`{id, reason}`）不归采集器，删除重插不影响它们；例外：`ant`、`sensenova` 为人工维护静态节（ADR 0013），任何 watcher 不得触碰。模型卡只来自 `data/all_models.json`，渠道 `cost` 逐字段优先；仅 Arena 允许使用文档化的 fallback 链。
- 渠道自带的 `claude-*` 模型不采集：Claude 由 AxonHub 自建全局模型 + Arena 映射供给（ADR 0012）；GPT 按名透传，不参与映射。
- 外部抓取只发生在 watch-pipeline；axonhub-admin 的规划阶段只消费仓库内快照做离线计算（不联网、不持凭据）；AxonHub 写入只在交互会话执行，CI 永不写 AxonHub、也不持有其凭据。
- CI workflow 安全：第三方 Actions 固定到已审核版本或 commit SHA、最小权限；不把抓取内容、commit message 或分支名拼进 shell 命令；CI 不执行测试，`pytest` 由维护者本地运行。
- `data/arena.json` 的 `manual: true` 记录与 `models_extra.json` 的 `aliases`、`blocklist` 是手工维护数据，采集/重生成必须保留（脚本已保证，不得绕过脚本直写文件）。
- `models.csv` 的 `role=request` 行是人工维护的请求模型清单（加行/删行即增减请求模型），其余单元格是生成物、每次运行整体重算。
- 保留无关的脏工作区改动；不手工编辑生成物。
- 凭据与写入门禁以 `.agents/skills/axonhub-admin/SKILL.md` 为准（token 纪律、确认门、事件响应）；门禁分级以 `CONTEXT.md` 为准。

## 项目元数据

- Issues: `docs/agents/issue-tracker.md`
- Triage labels: `docs/agents/triage-labels.md`
- Domain documentation: `docs/agents/domain.md`
