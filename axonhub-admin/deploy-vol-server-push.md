# vol-server 退役指南:快照推送线(ADR 0010 → ADR 0012)

本文件原是 ADR 0010 推送线的部署指南;该推送线已被 [ADR 0012](../docs/adr/0012-model-registry-pipeline.md) 退役——commandcode 渠道的 `supportedModels` 回归 axonhub-admin 交互式写入,不再有无人值守写入。以下是在 vol-server 上拆除已部署单元的步骤(需要在服务器上手动执行)。

## 拆除步骤

1. **停用并删除 systemd 单元**:

   ```bash
   sudo systemctl disable --now axonhub-push-channel-models.timer
   sudo rm /etc/systemd/system/axonhub-push-channel-models.timer
   sudo rm /etc/systemd/system/axonhub-push-channel-models.service
   sudo systemctl daemon-reload
   sudo systemctl reset-failed
   ```

2. **删除凭据与缓存**(推送线专用,不含其他服务的凭据):

   ```bash
   sudo rm -rf /var/lib/axonhub-push
   sudo rm /etc/axonhub-push/env /etc/axonhub-push/password
   sudo rmdir /etc/axonhub-push
   ```

3. **停用服务账号**:在 AxonHub 中禁用/删除推送专用的管理员账号(部署时的 `ops-push`)。确认 AxonHub 审计日志中该账号再无活动后即可删除。

4. **核实渠道状态**(可选,交互会话内经 axonhub-admin 执行):commandcode 渠道的 `autoSyncSupportedModels` 保持关闭;`supportedModels` 从此由 model-registry 规划 + axonhub-admin 确认式写入维护。

## 历史

- 该推送线部署于 2026-09(ADR 0010):每小时从本仓库 `main` 拉取 `data/goat-models.json`,经 `apply_channel_models.py` 幂等推送为 commandcode 渠道的 `supportedModels`。
- ADR 0012(model-registry 重组)将其退役:`apply_channel_models.py` 及其测试已从仓库删除;`data/goat-models.json` 已被 `data/models_extra.json` 取代。
