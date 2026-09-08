# vol-server 部署:快照推送 commandcode 渠道模型清单

落地 [`docs/adr/0010-snapshot-push-channel-lists.md`](../docs/adr/0010-snapshot-push-channel-lists.md):定时把 `data/goat-models.json` 的 `models` 键推成 AxonHub 渠道的 `supportedModels`。全部动作在 vol-server 上执行;CI(GitHub Actions)行为不变。

## 一次性准备

1. **代码**:克隆/更新本仓库到服务器(如 `/opt/models-mapping`,`main` 分支)。
2. **服务账号**:在 AxonHub 创建一个专用管理员账号(如 `ops-push`),仅用于本推送。
3. **凭据**(root-only):

   ```
   /etc/axonhub-push/env        # EnvironmentFile
   /etc/axonhub-push/password   # 0600,单行密码
   ```

   `env` 内容:

   ```ini
   AXONHUB_URL=http://127.0.0.1:8868
   AXONHUB_EMAIL=ops-push@example.com
   # 走 /etc/environment 里的代理拉 raw.githubusercontent.com:
   # https_proxy=http://127.0.0.1:7890
   ```

   密码只在 signin 时换取一次性 JWT,不写入任何日志或快照。

4. **渠道现状迁移**(任选其一,只需一次):
   - 用 `--dry-run` 跑一次脚本看 diff,然后正式运行——脚本会在同一 mutation 里写 `supportedModels` 并强制 `autoSyncSupportedModels=false`;
   - 渠道上残留的 `settings.modelListSourceURL`(已废弃的交集机制)在 AxonHub 管理界面手动清空一次即可,脚本不碰 settings(它是整体替换,交给人来改)。
5. **退役旧机制**:`systemctl disable --now axonhub-model-lists-refresh.timer`,删除 `/usr/local/bin/axonhub-refresh-model-lists`。

## systemd 单元

`/etc/systemd/system/axonhub-push-channel-models.service`:

```ini
[Unit]
Description=Push models-mapping GOAT snapshot to AxonHub channel list
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/axonhub-push/env
# 原子拉取快照(tmp+mv),失败即退出,不应用旧文件以外的任何东西
ExecStart=/bin/sh -c '\
  curl -fsSL --max-time 60 --retry 3 \
    https://raw.githubusercontent.com/jasonQin6/models-mapping/main/data/goat-models.json \
    -o /var/lib/axonhub-push/.goat-models.json.tmp && \
  mv /var/lib/axonhub-push/.goat-models.json.tmp /var/lib/axonhub-push/goat-models.json && \
  cd /opt/models-mapping && \
  python3 axonhub-admin/scripts/apply_channel_models.py \
    --source /var/lib/axonhub-push/goat-models.json \
    --channel commandcode-goat \
    --password-file /etc/axonhub-push/password \
    --expected-count 25:60'
```

`/etc/systemd/system/axonhub-push-channel-models.timer`(幂等,频繁运行无害;建议每小时):

```ini
[Unit]
Description=Hourly AxonHub channel model push

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo mkdir -p /var/lib/axonhub-push
sudo systemctl daemon-reload
sudo systemctl enable --now axonhub-push-channel-models.timer
sudo systemctl start axonhub-push-channel-models.service   # 立即跑一次验证
journalctl -u axonhub-push-channel-models.service -e       # 期望:updated and verified (N models)
```

注意:

- `--channel` 用服务器上的**实际渠道名**(按名精确匹配;重名或缺失脚本会拒绝)。`--expected-count 25:60` 与 `watch-pipeline/reference/goat/extra.json` 的 `expected_count` 保持一致,改契约时同步改这里。
- 拉取失败(404/网络)→ curl 非 0 → 本轮不 apply,渠道保留现状;apply 失败/验证失败 → 非 0 退出,timer 日志可见,下一轮自愈。
- 服务器上的仓库更新(`git pull`)可挂在同一 timer 前面或手动执行;脚本本身不依赖仓库数据,只依赖快照文件与命令行参数。
