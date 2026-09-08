# models-mapping

维护 OpenCode 模型目录数据，并把固定的 Claude 下游请求模型映射到可用的第三方模型。分工：CI（watch-pipeline.yml）只把外部数据抓取进仓库，`model-registry` 只做离线计算，`axonhub-admin` 才能写入 AxonHub。

本文件是数据归属与流程的唯一真源。产物归属、DAG、skills 分工以此处为准；术语以 [`CONTEXT.md`](CONTEXT.md) 为准，安全边界以 [`SECURITY.md`](SECURITY.md) 为准。

## 数据边界

| 文件 | 写入者 | 内容 |
|---|---|---|
| `data/all_models.json` | `watch-pipeline/fetch-all-models` | models.dev 全量目录快照（扁平 `vendor/model`），唯一的模型卡来源，不是任何清单来源 |
| `data/models_extra.json` | `fetch-opencode-go` + `watch-goat-models` | 渠道声明事实，按 `channels.<channel>.<model_id>` 组织：`rp5h`、`usage_quota`、`cost{}`、`context_threshold`、`peak_hours`、`retention`，goat 另有 `tok_s`；渠道自带的 `claude-*` 不采集（ADR 0012） |
| `data/arena.json` | `watch-pipeline/watch-arena` | Arena 评分、排名、effort 及名称匹配证据，`schema_version: 1` |
| `config/request-models.json` | 项目维护者 | 固定 request model 集合；只有 `claude-*` 参与映射，GPT 按名透传 |
| `config/model-decisions.json` | 项目维护者 | `scope.channels`（provider→channel）、人工 exclude/supplement、`mapping_overrides` |
| `models.csv` | `model-registry` | 可审查的映射建议表：候选行（全部去重后模型）+ `claude-*` request 行各一个建议 target；列定义以 `scripts/csv_io.py` 为准 |

`models.csv` 列为 `model_id,role,arena_score,rp5h,mapping`，其中 `role` 为 `candidate` 或 `request`，仅 request 行填写 `mapping`。catalog plan 是纯目标态 JSON（每渠道 bare-ID `supportedModels` 精确清单 + 模型卡目标值），无模式字段、无指纹、无远端 before-state——过期直接重新生成，不作为事实源持久留存。

去重规则：同一模型 id 被多个渠道声明时，只归 `rp5h` 最高的渠道（缺值输给有值，平局归字母序靠前的渠道），模型卡与 remark 都用胜出渠道的数据；每次归属判定都以 warning 披露。free 模型配额按归属渠道重算（`rp5h` = 该渠道最大非 free 值、`usage_quota` 缺省 60）。

## 常用命令

```bash
python3 model-registry/scripts/models_mapping.py --csv-output models.csv --plan-output /tmp/catalog-plan.json --fail-on-errors
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
                                    models.csv（人读）+ catalog plan（schema 2）
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
- `axonhub-admin`：唯一面向 AxonHub 写入、持有其凭据的 skill；按其 `SKILL.md` 的交互式执行程序（确认 → 读远端 → 逐项写入 → 回读验证 → 汇报）落地确认后的 catalog plan 与 `models.csv` 映射表；通用 channel 运维（quota tags、ordering weights、非固定模型 `channel_model` associations）经 `configure_channels.py`/`configure_models.py` dry-run 展示 diff 后由用户确认执行。
- `scripts/{csv_io,name_matching,parse_opencode_mdx}` 为共享库，无独立 skill 归属，由 `model-registry/scripts` 与 `watch-pipeline/scripts` 使用。

## 映射规则

普通 Claude request 使用 Arena 分数、RP5H 对数归一化和与 request 的接近度计算候选；价格和 usage quota 不参与本阶段 target selection。Claude series 中 Arena 最低的 request 为 baseline，直接映射归属渠道中 RP5H 最高的 free candidate（无 free 时选最高 RP5H）。本阶段不生成有序 fallback。

完整字段与评分见 [`data/formula.md`](data/formula.md)，术语与门禁见 [`CONTEXT.md`](CONTEXT.md)，架构取舍见 [`docs/adr/`](docs/adr)（当前 0005–0012；0012 为现行架构）。
