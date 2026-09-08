# Repository Guidelines

用中文回复用户。本仓约束分置四处，各有唯一职责；本文件常驻，其余按需披露。

系统分三层：采集（watch-pipeline：CI 只抓公开数据进仓库）→ 计算（model-registry：离线去重、补卡、Claude 映射建议与 catalog plan）→ 确认后写入（axonhub-admin）。每层只做自己的事；归属细节见 `README.md`。

## 何时读什么

- 术语、映射判断、门禁分级 → [`CONTEXT.md`](CONTEXT.md)
- 数据归属、流程、skills 分工、产物表 → [`README.md`](README.md)
- 凭据、workflow 安全、AxonHub 写入、事件响应 → [`SECURITY.md`](SECURITY.md)
- 改变拥有或写入边界 → [`docs/adr/`](docs/adr)（当前 0005–0012，0012 为现行架构），再改本文

## 命令

```bash
python3 model-registry/scripts/models_mapping.py --csv-output models.csv --plan-output /tmp/catalog-plan.json --fail-on-errors
python3 -m pytest -q
```

采集脚本位于 `watch-pipeline/scripts/`，由 `watch-pipeline` skill 维护并经 watch-pipeline.yml（每 3 天）执行；本地运行 watch_* 仅作调试，产物仍归对应渠道。go 与 goat 两个采集 job 都写 `data/models_extra.json` 的各自节，必须串行（CI 已按 go → goat 排序）。脚本失败会在 `watch-pipeline/reference/<channel>/last-error.json` 留痕，修复流程见 `watch-pipeline/SKILL.md`。skill 校验见各自 `SKILL.md`。

## 约束

- Python 3.12+，只用标准库；公开函数带类型注解。
- `data/models_extra.json` 各渠道节由对应采集脚本独占写入；模型卡只来自 `data/all_models.json`，渠道 `cost` 逐字段优先；仅 Arena 允许使用文档化的 fallback 链。
- 渠道自带的 `claude-*` 模型不采集：Claude 由 AxonHub 自建全局模型 + Arena 映射供给（ADR 0012）；GPT 按名透传，不参与映射。
- 外部抓取只发生在 watch-pipeline；model-registry 只消费仓库内快照做离线计算；AxonHub 写入只由 axonhub-admin 在交互会话执行，CI 永不写 AxonHub。
- 保留无关的脏工作区改动；不手工编辑生成物。
- 凭据与写入门禁以 `SECURITY.md` 为准；门禁分级以 `CONTEXT.md` 为准。

## 项目元数据

- Issues: `docs/agents/issue-tracker.md`
- Triage labels: `docs/agents/triage-labels.md`
- Domain documentation: `docs/agents/domain.md`
