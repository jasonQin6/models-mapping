# 安全边界

本文件是安全边界唯一真源。采集、变换、写入分为三层；数据归属与流程见 [`README.md`](README.md)，术语与门禁分级见 [`CONTEXT.md`](CONTEXT.md)，拥有变更见 [`docs/adr/`](docs/adr)。

三层边界：watch-pipeline.yml（GitHub Actions）只做公开数据抓取并提交仓库快照；`models-mapping` 只消费仓库内快照做数据变换与纯离线规划（不联网、不持凭据），`axonhub-admin` 是唯一面向 AxonHub 写入的 skill，必须经用户在交互会话内对确认材料的显式认可。写入与变换使用独立的确认材料，确认其一不授权另一。

## 凭据

- 采集 workflow 只访问公开上游，不持有 AxonHub JWT、API key、SQLite secret 或 SSH 私钥。
- `models-mapping` 的规划（build_mapping.py、sync_models.py）纯离线，不需要任何凭据。
- `axonhub-admin` 是唯一持有与使用 AxonHub 凭据的 skill，优先读取 `AXONHUB_JWT`；token 不打印、不复制、不写入 plan 或日志，plan 文件本身必须无凭据。
- JWT 铸造、TTL、签名等凭据政策只存在于 `axonhub-admin`；其他 skill 不复制该逻辑。
- 所有凭据通过环境变量或 GitHub Secrets 注入。不把 token 放入 JSON、CSV、plan、commit message 或 workflow 输出。

## Workflow 安全

- watch-pipeline.yml 中每个采集 job 只写自己的输出文件，输入仅来自固定公开 URL 与仓库内配置。归属以 `README.md` 为准。
- 不把 commit message、分支名或网页内容直接拼接进 shell 命令。
- 第三方 Actions 固定到已审核版本或 commit SHA，使用最小权限（通常为 `contents: write`）。
- 使用 concurrency 避免同一输出文件的并行提交；提交前执行 schema、格式与测试校验。

## AxonHub 写入

- 全部 AxonHub 变更由 `axonhub-admin` 在交互会话中按其 `SKILL.md` 的执行程序执行（ADR 0009）：AskUserQuestion 确认 → 读远端现状 → GraphQL 逐项变更 → 逐项回读验证 → 汇报。确认材料为 catalog plan 与 `models.csv`，二者独立确认，未经明确认可不执行写入。
- 写入输入来自 `models-mapping` 的离线产物（`sync_models.py` 的 plan JSON 与 `models.csv`）；plan 为纯目标态、无指纹与陈旧性机制，过期直接重新生成，远端漂移由 read-before-write 在执行时发现并报告。
- 变更范围以 `config/model-decisions.json` 的 managed scope 为准；scope 同时被变换与写入消费，变更时两侧复核。
- 四条护栏：只碰托管渠道；保留非托管关联与外部引用（被外部 channel 引用或被 association 精确引用的全局对象只保留并报告，不删除）；read-before-write；write-then-verify，无猜测性重试或无关回滚。
- 执行时核验托管渠道的 `autoSyncSupportedModels` 必须关闭——发现开启则报告并征询，不得在开启状态下写入策展清单。
- 写入仅替换已确认的渠道 `supportedModels`、模型卡目标值与 managed templates 的 `modelMappings`，保留维护集合外的人工 mappings、非映射字段与 remark 的 `manual` 内容；部分失败逐项报告。
- 写入只发生在交互式 agent 会话；CI（GitHub Actions）永不写 AxonHub，也不持有其凭据。

## 事件响应

如果怀疑凭据泄露：

1. 立即撤销并轮换 `AXONHUB_JWT`、GitHub Secrets 及相关服务器密钥。
2. 检查 Actions 日志、AxonHub 审计与服务日志、最近快照提交的 diff。
3. 核对模型卡、remark、channel supported-models 与 request associations 的变更。
4. 在确认完整性前保持 workflows disabled，并重新执行 dry-run 验证后再恢复。
