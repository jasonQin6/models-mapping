# 安全边界

本项目把公开数据采集、服务器同步和下游路由变更分成三个边界：

```text
GitHub Actions
  ├─ watch-go    → data/go.json
  ├─ watch-arena → data/arena.json
  └─ build-mapping → models.csv

服务器
  └─ opencode-axonhub-sync → AxonHub 模型目录

用户确认
  └─ models-mapping → request associations + stable/claude/gpt templates
```

## 凭据

- Actions 只访问公开 OpenCode/Arena 数据，不需要 AxonHub JWT、API key、SQLite secret 或 SSH 私钥。
- AxonHub skill 优先读取 `AXONHUB_JWT`。服务器本地后备 JWT 只在内存中生成；SQLite secret 不打印、不复制、不写入快照或日志。
- 所有凭据通过环境变量或 GitHub Secrets 注入。不要把 token 放进 JSON、CSV、plan、commit message 或 workflow 输出。

## Workflow 安全

- 每个 workflow 只写自己的输出文件；`build-mapping` 是唯一写 `models.csv` 的流程。
- workflow 输入只来自固定的公开 URL 和仓库内配置。不要把 commit message、分支名或网页内容直接拼接进 shell 命令。
- 第三方 Actions 固定到经过审核的版本或 commit SHA，并使用最小权限（通常为 `contents: write` 和必要的读取权限）。
- 使用 concurrency 避免同一输出文件发生并行提交；提交前执行 schema、格式和测试校验。

## AxonHub 写入

- sync 只处理 cache/go 交集以及 `model-decisions` 中已解决的模型；只修改三个 managed channels。协议、scope 或 schema 无效时停止。
- excluded 模型只从 managed channels 移除。外部 channel 使用会保留全局对象，association 引用会阻止删除；所有 deletes 必须出现在确认 plan 中。
- mapping 先生成 plan/dry-run，再展示完整 request→candidate 审核表。没有明确用户确认，不执行 `--apply`。
- mapping 只替换已确认 request model 的 association 集合和三个 managed templates 的 `modelMappings`；维护集合外的 template mappings 和非映射 profile 字段必须保留。
- apply 后重新读取并验证每个 request model；部分失败必须逐项报告，不进行猜测性重试或无关回滚。

## 事件响应

如果怀疑凭据泄露：

1. 立即撤销并轮换 `AXONHUB_JWT`、GitHub Secrets 及相关服务器密钥。
2. 检查 Actions 日志、AxonHub 审计/服务日志和最近的快照提交。
3. 核对模型卡、remark、channel supported-models 与 request associations 的 diff。
4. 在确认完整性前保持 workflows disabled，并重新执行 dry-run 验证。
