# models-mapping

维护 OpenCode provider 模型目录，并为固定的 Claude/GPT 下游请求模型生成可审核的一对一映射建议。目录同步与映射写入分开：GitHub Actions 只采集和计算，服务器上的 skill 才能更新 AxonHub。

## 数据边界

| 文件 | 所有者 | 内容 |
|---|---|---|
| `data/models.json` | `opencode-axonhub-sync` | 服务器执行 `opencode models <provider> --refresh --verbose` 得到的 provider 模型快照 |
| `data/go.json` | `watch-go` | `go.mdx` 补充的 rp5h、usage quota、价格和限制字段 |
| `data/arena.json` | `watch-arena` | Arena 评分、排名、effort 及模型名称匹配证据 |
| `config/request-models.json` | 项目维护者 | 预先配置的固定 Claude/GPT request model 集合 |
| `config/model-decisions.json` | 项目维护者 | 三渠道范围、交集内 exclude/supplement 和 mapping override |
| `models.csv` | `build-mapping` | 由三个快照确定性生成的审核工作区，不手工编辑 |

`models.csv` 只包含以下列：

```text
model_id,role,arena_score,rp5h,mapping
```

其中 `role` 为 `candidate` 或 `request`；只有 request 行填写 `mapping`。模型卡和 remark 所需的其他字段留在源 JSON 中，由同步 skill 写入 AxonHub。

## 流程

```text
watch-go ───────→ data/go.json ─┐
watch-arena ────→ data/arena.json ─┼→ build-mapping → models.csv
服务器 sync ────→ data/models.json ┘                         │
                                                             ▼
                                      models-mapping 审核 → 用户确认
                                                             │
                                                             ▼
                                      AxonHub type=model associations
                                      + stable/claude/gpt templates
```

两个采集 workflow 分别写入自己的 JSON；服务器 sync 提交第三份快照，只有 mapping builder 写 `models.csv`，以避免并行采集互相覆盖。workflow 不连接 AxonHub，也不持有 AxonHub 凭据。

## Skills

- `opencode-axonhub-sync`：以 cache/go 交集为目录，只维护三个 OpenCode channels、模型卡、remark 和 candidate 的 managed `channel_model` association；外部 channels 只读。
- `models-mapping`：审核 canonical mapping，确认后同步 request `type=model` associations 与 `stable`/`claude`/`gpt` templates，同时保留维护集合外的人工 mappings。
- `axonhub-config`：服务器端通用 channel 与非固定模型的运维。它不负责 request→candidate mapping 或 API-key profile template。

## 映射规则

普通 request model 使用 Arena 分数、RP5H 对数归一化和与 request 的接近度计算候选；价格和 usage quota 不参与本阶段 target selection。每个 Claude/GPT series 中 Arena 最低的 request model 是 baseline，沿用最高 RP5H 的 free candidate（没有 free 时选最高 RP5H candidate）这一特殊规则。本阶段暂不生成有序 fallback。

完整字段与评分约定见 [`data/formula.md`](data/formula.md)，术语见 [`CONTEXT.md`](CONTEXT.md)，架构取舍见 [`docs/adr/0005-three-source-mapping-and-confirmed-axonhub-writes.md`](docs/adr/0005-three-source-mapping-and-confirmed-axonhub-writes.md)。

## 安全边界

- Actions 只读取公开上游并提交 JSON 快照/生成 CSV；不得将 JWT、API key、SQLite secret 或 SSH 私钥写入仓库或日志。
- 服务器 sync 优先使用 `AXONHUB_JWT`；本机后备 JWT 只在服务器本地生成，secret 不打印、不复制、不持久化。
- AxonHub 写入前必须先 dry-run；mapping skill 必须展示完整审核表并等待用户明确确认。
- catalog 与 mapping 使用独立确认计划；mapping 只改已确认 request associations 和三个 managed templates，失败时报告逐项结果，不回滚无关配置。
