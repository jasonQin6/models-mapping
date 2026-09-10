# models-mapping

维护 OpenCode 模型目录数据，并把固定的 Claude 下游请求模型映射到可用的第三方模型。分工：CI（watch-pipeline.yml）只把外部数据抓取进仓库，`model-registry` 只做离线计算，`axonhub-admin` 才能写入 AxonHub。

本文件是数据归属与流程的唯一真源。产物归属、DAG、skills 分工以此处为准；术语以 [`CONTEXT.md`](CONTEXT.md) 为准，安全边界以 [`SECURITY.md`](SECURITY.md) 为准，登记与映射规则以 [`.agents/skills/model-registry/reference/`](.agents/skills/model-registry/reference/) 为准。

## 数据边界

| 文件 | 写入者 | 内容 |
|---|---|---|
| `data/all_models.json` | `watch-pipeline/fetch-all-models` | models.dev 全量目录快照；唯一的模型卡来源，不是任何清单来源 |
| `data/models_extra.json` | `fetch-opencode-go` + `watch-goat-models`（契约字段）；项目维护者（`exclude` 字段与顶层 `aliases`） | 各渠道声明的模型事实（配额、价格）与人工排除标记，按 `channels.<channel>.<model_id>` 组织；字段契约以各采集脚本为准，采集按字段合并、契约外字段人工所有（ADR 0014） |
| `data/channel-plan.json` | `model-registry`（整体重算） | 每渠道 `supportedModels` 权威期望态（schema 1）：替代 AxonHub 的 autoSync，手动维护的"有权限的模型"清单（ADR 0017） |
| `data/model-plan.json` | `model-registry`（整体重算） | 模型级增量计划（schema 1）：`cardRef` 引用 `all_models.json`、cost 终值、remark、派生 meta、`channelPriority` 关联链；不复制卡片内容（ADR 0016/0017） |
| `data/arena.json` | `watch-pipeline/watch-arena` | Arena 评分与名称匹配证据；含人工赋分记录（规则见 `watch_arena.py`） |
| `models.csv` | `model-registry`（重算全部单元格）；项目维护者（`role=request` 行清单：加行/删行） | 映射审查表：request 行是人工维护的请求模型清单（事实源），其余为生成物（列定义以 `.agents/skills/model-registry/scripts/csv_io.py` 为准） |

channel-plan 与 model-plan 是整体重算的生成物（schema 1，提交入库、人工不编辑，渠道键 = `models_extra.json` 渠道节名）。模型如何从渠道声明变为登记清单、映射如何分配，完整规则按 AxonHub 的三类对象见 [`.agents/skills/model-registry/reference/`](.agents/skills/model-registry/reference/)：[渠道清单](.agents/skills/model-registry/reference/channel.md)（去重、变种治理、free 补全）、[模型卡](.agents/skills/model-registry/reference/models.md)（增量与写时组装）、[模型关联](.agents/skills/model-registry/reference/associations.md)（赋分来源、公式与 free 填充顺序、非 Claude 路由约定）。

## 常用命令

```bash
python3 .agents/skills/model-registry/scripts/models_mapping.py --csv models.csv --fail-on-errors
python3 -m pytest -q
```

计算纯离线，不需要任何凭据；默认写出 `models.csv` 与 `data/{channel-plan,model-plan}.json`。模型卡终态在写入前用 `assemble_card.py` 组装：

```bash
python3 .agents/skills/model-registry/scripts/assemble_card.py --id <modelID>
```

写入在 `axonhub-admin` 的交互会话内按其 `SKILL.md` 的执行程序进行。

## 流程

```text
fetch-all-models ──→ data/all_models.json ┐
fetch-opencode-go ─┐                       │
                   ├→ data/models_extra.json ├→ model-registry 离线规划：
watch-goat-models ─┘   （渠道声明事实）      │    去重 → free 补全 → 补卡
watch-arena ────────→ data/arena.json ─────┘    → Claude 映射建议
                                                 ↓
                     models.csv（人读）+ data/channel-plan.json + data/model-plan.json
                                                 │
                              ┌──────────────────┼──────────────────────┐
                              ▼                  ▼                      ▼
              模型卡 + 渠道 supportedModels   Claude 映射写入        日常换映射
              → axonhub-admin 确认式写入     用户读 models.csv 后    直接在 AxonHub UI
                （读远端 → 逐项写 → 回读）    选 agent 写或 UI 手改   axon.jasonqin.site/models
```

Claude 全局模型（claude-opus-5、claude-sonnet-5 等）是 AxonHub 中一次性人工创建的对象，不进任何脚本；渠道自带的 `claude-*` 模型不采集，下游 Claude 请求全部经映射路由到第三方模型。采集与计算全程离线：workflow 不连接 AxonHub，`model-registry` 不持凭据，CI 永不写 AxonHub；AxonHub 的所有写入都由 `axonhub-admin` 在交互会话内经用户确认后执行。

## Skills

- `watch-pipeline`：维护 watch-pipeline.yml、采集脚本（`.agents/skills/watch-pipeline/scripts/watch_*.py`、`models_extra.py`）与全部采集渠道；每渠道的字段契约就是采集脚本的解析函数（无独立契约文件），脚本失败留痕于 `.agents/skills/watch-pipeline/reference/<channel>/last-error.json`（目录由首次失败创建、成功后自删，健康仓库中不存在），修复流程见其 `SKILL.md`。
- `model-registry`：把 `data/*.json` 离线变换为去重后的模型登记与 AxonHub 写入材料——`models.csv` 映射建议、channel-plan 与 model-plan；只读规划，不联网，不写 AxonHub，详见其 `SKILL.md`。
- `axonhub-admin`：唯一面向 AxonHub 写入、持有其凭据的 skill；按其 `SKILL.md` 的交互式执行程序（确认 → 读远端 → 逐项写入 → 回读验证 → 汇报）落地确认后的 channel-plan/model-plan（按对象分别确认）与 `models.csv` 映射表，以及日常 channel/model 运维。渠道的 tags/weights 等期望状态以 AxonHub 系统内现状为准，不在仓库中另存副本。
- 名字归一与匹配只在计算层发生：`.agents/skills/model-registry/scripts/{csv_io,name_matching}.py` 归 `model-registry`（`csv_io` 是 models.csv 列契约，`name_matching` 是 Arena 名归一与匹配链）。采集层只存原始事实，名字归一在规划装载时进行。
