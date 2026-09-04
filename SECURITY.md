# 安全边界

本文件是安全边界唯一真源。采集、变换、写入分为三层；数据归属与流程见 [`README.md`](README.md)，术语与门禁分级见 [`CONTEXT.md`](CONTEXT.md)，拥有变更见 [`docs/adr/`](docs/adr)。

三层边界：watch-pipeline.yml（GitHub Actions）只做公开数据抓取并提交仓库快照；`models-mapping` 只消费仓库内快照做数据变换，不联网抓取、不持有凭据；`axonhub-admin` 是唯一面向 AxonHub 写入的 skill，必须经用户显式确认。写入与变换使用独立的确认材料，确认其一不授权另一。

## 凭据

- 采集 workflow 只访问公开上游，不持有 AxonHub JWT、API key、SQLite secret 或 SSH 私钥。
- `axonhub-admin` 优先读取 `AXONHUB_JWT`。本地后备 JWT 仅在内存中生成；SQLite secret 不打印、不复制、不写入快照或日志。
- 所有凭据通过环境变量或 GitHub Secrets 注入。不把 token 放入 JSON、CSV、plan、commit message 或 workflow 输出。

## Workflow 安全

- watch-pipeline.yml 中每个采集 job 只写自己的输出文件，输入仅来自固定公开 URL 与仓库内配置。归属以 `README.md` 为准。
- 不把 commit message、分支名或网页内容直接拼接进 shell 命令。
- 第三方 Actions 固定到已审核版本或 commit SHA，使用最小权限（通常为 `contents: write`）。
- 使用 concurrency 避免同一输出文件的并行提交；提交前执行 schema、格式与测试校验。

## AxonHub 写入

- 全部 AxonHub 变更由 `axonhub-admin` 执行：先生成 plan/dry-run 并展示影响摘要，未经用户对确认材料的明确认可不执行写入。
- 写入输入来自 `models-mapping` 的变换产物（如 `models.csv` 及其审核材料）；发现 schema、scope 或指纹不符时停止。
- 变更范围以 `config/model-decisions.json` 的 managed scope 为准；scope 同时被变换与写入消费，变更时两侧复核。
- 被排除模型仅从 managed channels 移除；被外部 channel 引用或被 association 精确引用的全局对象不进入删除，仅未被引用且未被外部使用的对象才可进入已确认的删除计划。
- 写入仅替换已确认的 association 集合与 managed templates 的 `modelMappings`，保留维护集合外的人工 mappings 与非映射字段。
- 写入后重新读取并逐项验证；部分失败逐项报告，不做猜测性重试或无关回滚。

## 事件响应

如果怀疑凭据泄露：

1. 立即撤销并轮换 `AXONHUB_JWT`、GitHub Secrets 及相关服务器密钥。
2. 检查 Actions 日志、AxonHub 审计与服务日志、最近快照提交的 diff。
3. 核对模型卡、remark、channel supported-models 与 request associations 的变更。
4. 在确认完整性前保持 workflows disabled，并重新执行 dry-run 验证后再恢复。
