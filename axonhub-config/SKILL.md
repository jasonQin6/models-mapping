---
name: axonhub-config
description: 在 AxonHub 服务器上维护通用 channel 配置和非固定模型的 channel associations。request model 到 OpenCode candidate 的一对一映射由 models-mapping skill 负责。
---

# AxonHub Config

这个 skill 只做 AxonHub 的通用运维，并且必须在 AxonHub 服务器上执行。它维护 channel 的 tags/ordering weight，以及非固定模型根据可用 channel 生成的 `channel_model` associations。

## 边界

- `models-mapping` 负责固定 Claude/GPT request→candidate 映射，并通过确认计划写入 `type=model` associations 与 `stable`/`claude`/`gpt` templates。
- `models-mapping` 的 `sync_models.py` 负责目录 plan：从快照产出渠道 `supportedModels`、model card、remark 和 candidate 的 managed `channel_model` association 的变更计划；执行归 `axonhub-admin` 的 `apply_catalog_plan.py`。
- 通用流程不读取 mapping CSV，不修改 managed templates 或固定 request associations。
- `configure_models.py` 只处理非固定模型的通道选择；它不会处理 Claude/GPT request models。

## 通用配置流程

先执行 dry-run，检查当前 AxonHub 状态与预期 diff：

```bash
python3 axonhub-config/scripts/configure_channels.py \
  --axonhub-url "$AXONHUB_URL" \
  --dry-run

python3 axonhub-config/scripts/configure_models.py \
  --axonhub-url "$AXONHUB_URL" \
  --dry-run
```

只有用户明确要求应用并核对过预览后，才去掉 `--dry-run`。应用后重新读取 channels/models，确认变更只落在预期对象。

## Request mapping helper

为了让仓库内的 mapping skill 使用稳定入口，`scripts/apply_mapping.py` 同步一对一 associations 与三个 managed templates：

```bash
python3 axonhub-config/scripts/apply_mapping.py \
  --mapping-file models.csv \
  --axonhub-url "$AXONHUB_URL" \
  --plan-output /tmp/models-mapping-plan.json \
  --dry-run
```

它不属于本 skill 的决策流程；只有 `models-mapping` 展示 plan hash 并获得用户确认后，才允许传入已审核 plan。helper 会分页读取模型和 templates、检查 fingerprints、保留 template 的人工 mappings/非映射字段，并将确认的 request association 精确收敛为一个 enabled `type=model`。

## 凭据

优先使用环境变量 `AXONHUB_JWT`，不要把 token 写进文件或命令历史。服务器上的本地后备 JWT 由 sync helper 按需生成，SQLite secret 不输出、不复制、不持久化。

## 参考

- [Channel configuration](references/channels.md)
- [Non-fixed model associations](references/associations.md)
- [Migration between instances](references/migration.md)
- [Models mapping skill](../models-mapping/SKILL.md)
