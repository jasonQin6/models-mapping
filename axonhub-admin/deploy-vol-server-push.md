# vol-server 推送/刷新线退役执行记录(ADR 0010 → ADR 0012)

本文件原是 ADR 0010 推送线的部署指南;该线连同更早的 modelListSourceURL 刷新机制已按 [ADR 0012](../docs/adr/0012-model-registry-pipeline.md) 全部退役。**退役动作已于 2026-09-08 由 agent 经 `ssh klein@vol-server` 执行完毕**,以下为实际状态与执行记录,供日后核对。

## 执行记录(2026-09-08)

1. **发现**:ADR 0010 的推送单元(`axonhub-push-channel-models.timer/service`、`/etc/axonhub-push/`、`/var/lib/axonhub-push/`)在这台服务器上**从未部署过**。实际在跑的是更早的刷新机制:`axonhub-model-lists-refresh.timer`(每小时)以 klein 用户跑 `/usr/local/bin/axonhub-refresh-model-lists`,从 `jasonQin6/commandcode-goat-sync` 仓库拉快照到 `/home/klein/.config/axonhub/model-lists/goat-models.json`,供 AxonHub 渠道 settings 的 `modelListSourceURL` 消费。该刷新自 2026-09-07 起因上游 SSL 故障持续失败。
2. **拆除刷新线**:`systemctl disable --now axonhub-model-lists-refresh.timer`,删除 timer/service 单元与 `/usr/local/bin/axonhub-refresh-model-lists`,`daemon-reload` + `reset-failed`;删除缓存目录 `model-lists/`。服务器上现仅剩 `axonhub.service`(AxonHub 本体)。
3. **关闭渠道自动同步**:经 admin GraphQL(`updateChannel(id: "gid://axonhub/Channel/12", input: {autoSyncSupportedModels: false})`)关闭 commandcode-goat 的 autoSync,回读验证 `autoSyncSupportedModels=false`、`supportedModels`(59 项,旧机制产物)未动。其余渠道(mimo 同为 autoSync=1)不属于本项目管理,未触碰。
4. **modelListSourceURL 死键**:commandcode-goat 的 DB settings 里仍存有 `modelListSourceURL` 键,但当前 AxonHub 二进制中已无该字符串(功能整体移除),GraphQL 的 `ChannelSettings` 也不再暴露它——确认为无害死数据,为避免直写运行中的 SQLite 未做清理。

## 现状与后续

- commandcode 渠道(及其他全部渠道)的 `supportedModels` 从此只经 model-registry 规划 + axonhub-admin 交互式写入维护,不再存在任何无人值守写入。
- 渠道上现存的 59 项 `supportedModels` 是旧交集机制的产物;新架构(`data/models_extra.json` 去重)规划出 35 项,需在 axonhub-admin 交互会话中经用户确认后整体替换。
- 该渠道 `settings.modelListSourceURL` 的死键如需清除,待 AxonHub 后续版本经管理界面或 API 自然处理。
