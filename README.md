# models-mapping

维护 OpenCode 模型目录数据，并把固定的 Claude 下游请求模型映射到可用的第三方模型。分工：CI（watch-pipeline.yml）只把外部数据抓取进仓库，`model-registry` 只做离线计算，`axonhub-admin` 才能写入 AxonHub。

本文件是数据归属与流程的唯一真源。产物归属、DAG、skills 分工以此处为准；术语以 [`CONTEXT.md`](CONTEXT.md) 为准，安全边界以 [`SECURITY.md`](SECURITY.md) 为准，登记与映射规则以 [`model-registry/reference/`](model-registry/reference/) 为准。

## 数据边界

| 文件 | 写入者 | 内容 |
|---|---|---|
| `data/all_models.json` | `watch-pipeline/fetch-all-models` | models.dev 全量目录快照；唯一的模型卡来源，不是任何清单来源 |
| `data/models_extra.json` | `fetch-opencode-go` + `watch-goat-models`（契约字段）；项目维护者（`exclude` 字段与顶层 `aliases`） | 各渠道声明的模型事实（配额、价格）与人工排除标记，按 `channels.<channel>.<model_id>` 组织；字段契约以各采集脚本为准，采集按字段合并、契约外字段人工所有（ADR 0014） |
| `data/arena.json` | `watch-pipeline/watch-arena` | Arena 评分与名称匹配证据；含人工赋分记录（规则见 `watch_arena.py`） |
| `models.csv` | `model-registry`（重算全部单元格）；项目维护者（`role=request` 行清单：加行/删行） | 映射审查表：request 行是人工维护的请求模型清单（事实源），其余为生成物（列定义以 `scripts/csv_io.py` 为准） |

catalog plan 是纯目标态 JSON（过期直接重新生成，不作为事实源持久留存；schema 3，渠道键 = `models_extra.json` 渠道节名）。模型如何从渠道声明变为登记清单、映射如何分配，完整规则按 AxonHub 的三类对象见 [`model-registry/reference/`](model-registry/reference/)：[渠道清单](model-registry/reference/channel.md)（去重、变种治理、free 补全）、[模型卡](model-registry/reference/models.md)、[模型关联](model-registry/reference/associations.md)（赋分来源、公式与 free 填充顺序、非 Claude 路由约定）。

## 常用命令

```bash
python3 model-registry/scripts/models_mapping.py --csv models.csv --plan-output /tmp/catalog-plan.json --fail-on-errors
python3 -m pytest -q
```

计算纯离线，不需要任何凭据；写入在 `axonhub-admin` 的交互会话内按其 `SKILL.md` 的执行程序进行。

## 流程

```text
fetch-all-models ──→ data/all_models.json ┐
fetch-opencode-go ─┐                       │
                   ├→ data/models_extra.json ├→ model-registry 离线规划：
watch-goat-models ─┘   （渠道声明事实）      │    去重 → free 补全 → 补卡
watch-arena ────────→ data/arena.json ─────┘    → Claude 映射建议
                                                 ↓
                                    models.csv（人读）+ catalog plan（schema 3）
                                                 │
                              ┌──────────────────┼──────────────────────┐
                              ▼                  ▼                      ▼
              模型卡 + 渠道 supportedModels   Claude 映射写入        日常换映射
              → axonhub-admin 确认式写入     用户读 models.csv 后    直接在 AxonHub UI
                （读远端 → 逐项写 → 回读）    选 agent 写或 UI 手改   axon.jasonqin.site/models
```

Claude 全局模型（claude-opus-5、claude-sonnet-5 等）是 AxonHub 中一次性人工创建的对象，不进任何脚本；渠道自带的 `claude-*` 模型不采集，下游 Claude 请求全部经映射路由到第三方模型。采集与计算全程离线：workflow 不连接 AxonHub，`model-registry` 不持凭据，CI 永不写 AxonHub；AxonHub 的所有写入都由 `axonhub-admin` 在交互会话内经用户确认后执行。

## Skills

- `watch-pipeline`：维护 watch-pipeline.yml、采集脚本（`watch-pipeline/scripts/watch_*.py`、`models_extra.py`）与全部采集渠道；每渠道字段契约在 `watch-pipeline/reference/<channel>/extra.json`，脚本失败留痕于 `reference/<channel>/last-error.json`（成功后自删），修复流程见其 `SKILL.md`。
- `model-registry`：把 `data/*.json` 离线变换为去重后的模型登记与 AxonHub 写入材料——`models.csv` 映射建议、catalog plan；只读规划，不联网，不写 AxonHub，详见其 `SKILL.md`。
- `axonhub-admin`：唯一面向 AxonHub 写入、持有其凭据的 skill；按其 `SKILL.md` 的交互式执行程序（确认 → 读远端 → 逐项写入 → 回读验证 → 汇报）落地确认后的 catalog plan 与 `models.csv` 映射表，以及日常 channel/model 运维。渠道的 tags/weights 等期望状态以 AxonHub 系统内现状为准，不在仓库中另存副本。
- `scripts/{csv_io,name_matching,parse_opencode_mdx}` 为共享库，无独立 skill 归属，由 `model-registry/scripts` 与 `watch-pipeline/scripts` 使用。
