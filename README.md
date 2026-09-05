# models-mapping

维护 OpenCode 模型目录数据，并把固定的 Claude/GPT 下游请求模型变换为 AxonHub 需要的数据格式。采集、变换、写入三层分开：watch-pipeline.yml 只把外部数据抓取进仓库，`models-mapping` 只做数据变换，`axonhub-admin` 才能写入 AxonHub。

本文件是数据归属与流程的唯一真源。产物归属、DAG、skills 分工以此处为准；术语以 [`CONTEXT.md`](CONTEXT.md) 为准，安全边界以 [`SECURITY.md`](SECURITY.md) 为准。

## 数据边界

| 文件 | 写入者 | 内容 |
|---|---|---|
| `data/all_models.json` | `watch-pipeline/fetch-all-models` | `models.dev/models.json` 全量 provider 目录快照 |
| `data/opencode-go-models.json` | `watch-pipeline/fetch-opencode-go` | `models.dev/api.json` 的 `opencode-go` provider 经 `go.mdx` 补充 rp5h、usage quota、价格和限制字段 |
| `data/arena.json` | `watch-pipeline/watch-arena` | Arena 评分、排名、effort 及名称匹配证据，`schema_version: 1` |
| `data/goat-models.json` | `watch-pipeline/watch-goat-models` | Command Code GOAT 套餐快照（`commandcode-goat` provider），与 opencode-go 并集构成候选宇宙 |
| `config/request-models.json` | 项目维护者 | 固定的 Claude/GPT request model 集合 |
| `config/model-decisions.json` | 项目维护者 | 候选 exclude/supplement 和 mapping override |
| `data/enriched.json` | `build-mapping` | 派生补全字段（free 模型 rp5h/usage_quota）及来源标注，可随时重算 |
| `models.csv` | `build-mapping` | 由快照确定性生成的审核工作区，不手工编辑；列定义以 `scripts/csv_io.py` 为准 |

`models.csv` 列为 `model_id,role,arena_score,rp5h,mapping`，其中 `role` 为 `candidate` 或 `request`，仅 request 行填写 `mapping`。其他字段留在源 JSON 中，由 `axonhub-admin` 写入 AxonHub。候选宇宙 = `opencode-go-models.json ∪ goat-models.json` − request models − 人工 excludes；free 模型缺 rp5h 取同渠道非 free 最大值、缺 usage_quota 补 60（记录进 `data/enriched.json`）。

`models.csv` 是唯一映射审查产物；catalog plan 是纯目标态 JSON（每渠道 bare-ID `supportedModels` 精确清单 + 模型卡目标值），无模式字段、无指纹、无远端 before-state——过期直接重新生成，不作为事实源持久留存。托管范围（provider→channel 映射与 managed templates）以 `config/model-decisions.json` 的 `scope` 为唯一事实源。

## 常用命令

```bash
python3 models-mapping/scripts/build_mapping.py --model-decisions config/model-decisions.json --fail-on-errors
python3 models-mapping/scripts/sync_models.py --source data/opencode-go-models.json --source data/goat-models.json --plan-output /tmp/catalog-plan.json
python3 -m pytest -q
```

规划纯离线，不需要任何凭据；写入在 `axonhub-admin` 的交互会话内按其 `SKILL.md` 的执行程序进行。

## 流程

```text
fetch-all-models ─→ data/all_models.json ─┐
fetch-opencode-go → data/opencode-go-models.json ─┼→ build-mapping → models.csv + data/enriched.json
watch-arena ──────→ data/arena.json ───────┘                         │
watch-goat-models → data/goat-models.json ┘                         ▼
                      models-mapping 离线规划：补全 + 映射建议 + catalog plan（纯目标态，无指纹，过期直接重生成）
                                                                     │
                                                                     ▼
                                        会话内确认：AskUserQuestion，plan 与 models.csv 独立确认
                                                                     │
                                                                     ▼
                                        axonhub-admin 按执行程序写入并逐项回读验证（ADR 0009）
```

采集 jobs 分别写入自己的 JSON；只有 `build-mapping` 写 `models.csv`，避免并行覆盖。采集与规划全程离线：workflow 不连接 AxonHub，models-mapping 不持有凭据，CI 永不写 AxonHub；AxonHub 写入只由 `axonhub-admin` 在交互会话内经用户确认后执行。

## Skills

- `watch-pipeline`：维护 watch-pipeline.yml、采集脚本（`watch-pipeline/scripts/watch_*.py`）与全部采集渠道；每渠道字段契约在 `watch-pipeline/reference/<channel>/extra.json`，脚本失败留痕于 `reference/<channel>/last-error.json`（成功后自删），修复流程见其 `SKILL.md`。原 `commandcode-goat-scraper` 及 models-mapping、opencode-axonhub-sync 中的采集部分已并入。
- `models-mapping`：把 `data/*.json` 离线变换为 AxonHub 需要的数据格式——free 模型补全（`data/enriched.json`）、Claude/GPT 映射建议（`models.csv`）、目录同步 plan（`sync_models.py`，纯离线无凭据）；只读规划，不联网，不写 AxonHub。
- `axonhub-admin`：唯一面向 AxonHub 写入、持有其凭据的 skill；按其 `SKILL.md` 的交互式执行程序（确认 → 读远端 → 逐项写入 → 回读验证 → 汇报）落地确认后的 catalog plan 与 `models.csv` 映射表；通用 channel 运维（quota tags、ordering weights、非固定模型 `channel_model` associations）经 `configure_channels.py`/`configure_models.py` dry-run 展示 diff 后由用户确认执行。原 `axonhub-config` 已并入本 skill。
- `scripts/{csv_io,name_matching,parse_opencode_mdx}` 为共享库，无独立 skill 归属，由 `models-mapping/scripts` 与 `watch-pipeline/scripts` 使用。

## 映射规则

普通 request 使用 Arena 分数、RP5H 对数归一化和与 request 的接近度计算候选；价格和 usage quota 不参与本阶段 target selection。每个 series 中 Arena 最低的 request 为 baseline，沿用最高 RP5H 的 free candidate（无 free 时选最高 RP5H）。本阶段不生成有序 fallback。

完整字段与评分见 [`data/formula.md`](data/formula.md)，术语与门禁见 [`CONTEXT.md`](CONTEXT.md)，架构取舍见 [`docs/adr/0005-three-source-mapping-and-confirmed-axonhub-writes.md`](docs/adr/0005-three-source-mapping-and-confirmed-axonhub-writes.md) 与 [`docs/adr/0006-goat-as-fourth-snapshot.md`](docs/adr/0006-goat-as-fourth-snapshot.md)。
