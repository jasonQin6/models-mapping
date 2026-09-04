# Repository Guidelines

用中文回复用户。本仓约束分置四处，各有唯一职责；本文件常驻，其余按需披露。

系统分四层：采集（watch-pipeline）→ 变换（models-mapping）→ 确认（用户）→ 写入（axonhub-admin）。每层只做自己的事；归属细节见 `README.md`。

## 何时读什么

- 术语、映射判断、门禁分级 → [`CONTEXT.md`](CONTEXT.md)
- 数据归属、流程、skills 分工、产物表 → [`README.md`](README.md)
- 凭据、workflow 安全、AxonHub 写入、事件响应 → [`SECURITY.md`](SECURITY.md)
- 改变拥有或写入边界 → [`docs/adr/`](docs/adr)（当前 0005、0006），再改本文

## 命令

```bash
python3 models-mapping/scripts/build_mapping.py --model-decisions config/model-decisions.json --fail-on-errors
python3 -m pytest -q
```

采集脚本由 `watch-pipeline` skill 维护并经 watch-pipeline.yml 执行；本地运行 watch_* 仅作调试，产物仍归对应渠道。skill 校验见各自 `SKILL.md`。

## 约束

- Python 3.12+，只用标准库；公开函数带类型注解。
- 保持 `all_models.json` 与 `opencode-go-models.json` 的源标识符精确一致；仅 Arena 允许使用文档化的 fallback 链。
- 外部抓取只发生在 watch-pipeline；models-mapping 只消费仓库内快照做变换；AxonHub 写入只由 axonhub-admin 执行。
- 保留无关的脏工作区改动；不手工编辑生成物。
- 凭据与写入门禁以 `SECURITY.md` 为准；门禁分级以 `CONTEXT.md` 为准。

## 项目元数据

- Issues: `docs/agents/issue-tracker.md`
- Triage labels: `docs/agents/triage-labels.md`
- Domain documentation: `docs/agents/domain.md`
