# model-registry 重构实施方案（修订版：保留 watch-pipeline，计算收敛一个 skill）

## 0. 分工

```
CI（watch-pipeline.yml，保留，cron 改约每 3 天）—— 只获取数据：
  fetch-all-models   → data/all_models.json    （不变，curl models.dev，只填模型卡）
  fetch-opencode-go  → data/models_extra.json["opencode-go"]      （watch_go.py 原地简化）
  watch-goat-models  → data/models_extra.json["commandcode-goat"] （watch_goat.py 原地简化；needs go job，串行写同文件）
  watch-arena        → data/arena.json        （不变）

Agent（交互会话）—— 读取、计算、展示：
  model-registry/scripts/models_mapping.py（新，离线无凭据）
    ① 去重：同模型 id 跨渠道只归 rp5h 最高渠道（null 输给有值，平局归靠前渠道，记 warning）
    ② free 补全（按胜出渠道：缺 rp5h 补该渠道最大非 free 值、usage_quota 补 60；provenance 走 warning 展示）
    ③ 模型卡从 all_models.json 填（缺卡不造数据，warning）
    ④ Claude 打分映射（沿用 score_match：0.35*arena+0.30*log_rp5h+0.35*proximity−0.2*downgrade+0.1*upgrade；baseline 规则保留）→ models.csv（request 仅 claude-* 行；GPT 不参与映射）
    ⑤ plan（沿用 schema 2 结构 providers/channels/models[]/removals/warnings）→ 展示给用户 → axonhub-admin 确认式写入
  映射日常变更：你在 https://axon.jasonqin.site/models 手改；Claude 全局模型一次性人工创建，不进脚本
```

计算不进 CI：`build-mapping.yml` 删除（models.csv 由 Agent 会话计算并提交）。

## 1. watch-pipeline 原地简化

**watch_go.py**：输入仅 go.mdx；输出 models_extra.json 的 `opencode-go` 节（read-modify-write：读旧文件、更新本渠道节、原子写回）。每模型字段：`rp5h, usage_quota, cost{input,output,cache_read,cache_write}（由 go.mdx 价格四列组装，free 模型置 0）, context_threshold, peak_hours, retention`（后三者有则记、无则 null）。删：`--models-dev`、models.dev 卡片字段、extra 双写、顶层 price_*。**跳过 claude-\* 模型**（完全不采集）。free 的 rp5h 补全移到规划层。复用 `scripts/parse_opencode_mdx.py` 不变。
**watch_goat.py**：输出 `commandcode-goat` 节：`rp5h, usage_quota, tok_s, cost{}`（GOAT 交易价）。删 `--all-models` 静态合并（不再要卡片字段）；保留 expected_count 硬门禁、intelligence 筛选、to_model_id 锚点还原、last-error 留痕；**跳过 claude-\***。
**watch_arena.py / error_state.py / fetch-all-models job**：不动。
**契约** `watch-pipeline/reference/{go,goat}/extra.json` 重写为新字段与新路径语义。
**workflow**：cron `0 0 */3 * *`；go job 去掉抓 models.dev api.json 的步骤；goat job 改 `needs: fetch-opencode-go`（两 job 写同一文件，必须串行）；其余不变。

`data/models_extra.json`：
```json
{"schema_version": 1, "updated_at": "...",
 "channels": {
   "opencode-go":      {"<id>": {"rp5h":7600,"usage_quota":30.0,"tok_s":null,
       "cost":{"input":0.22,"output":0.66,"cache_read":0.007,"cache_write":null},
       "context_threshold":null,"peak_hours":null,"retention":null}},
   "commandcode-goat": {"<id>": {"rp5h":18200,"usage_quota":60.0,"tok_s":129,
       "cost":{"input":0.22,"output":0.66,"cache_read":0.007,"cache_write":0.0}}}}}
```

## 2. 新计算 skill：model-registry/（名字可改）

```
model-registry/
  SKILL.md              # 职责：读 data/*.json → models_mapping.py 计算 → 展示 models.csv 与 plan → 交接 axonhub-admin
  scripts/models_mapping.py   # 吸收 select.py 去重 + build_mapping 打分/baseline/Arena匹配/free补全/门禁 +
                              #   sync_models model_card/_model_meta/remark/plan 组装；stdlib-only、类型注解、单文件
  tests/test_models_mapping.py
```

CLI：`--extra --cards --arena --request-models --decisions --csv-output models.csv --plan-output PATH --fail-on-errors`。plan 与现有 schema 2 同构（supportedModels 整体替换清单 + 模型卡 + remark），axonhub-admin 执行程序基本不动。共享库 `scripts/{csv_io,name_matching}.py` 继续共用不复制。config/model-decisions.json 保留 exclude/supplement/mapping_overrides，仅删 `scope.external_channel_lists` 键（commandcode 回归交互式写入）。

## 3. 实施步骤（提交序列）

1. **baseline 两笔**：先写精简 `docs/adr/0011-go-mdx-owns-opencode-go-model-list.md`（~20 行）；commit A = ADR 0010 拨改动（含 select.py+test），commit B = ADR 0011 拨改动 + 0011 ADR。`sync_models.py`/`test_sync_models.py` 两拨混在同一文件，用 `git add -p` 分 hunk，纠缠则并为一笔注明。untracked 的 `data/model_select.json`、`data/opencode-go-models-2.json` 不提交直接删。
2. **采集简化**：watch_go/watch_goat 改造 + 各自测试重写 + 契约更新 + workflow（3 天 cron、串行化、去 models.dev 步骤）；本地真数据生成首版 models_extra.json 并抽查（deepseek-v4-flash 两渠道数值、claude-* 不出现、go.mdx 独有模型只带渠道字段）。
3. **计算 skill 落地**：model-registry/ 全量 + 本地跑通（`--fail-on-errors` 无 blocking）；models.csv 重算并与旧表对照（候选池去掉 claude 渠道模型与 go 独有 7 模型，映射结果变化属预期）；抽查 plan（两渠道 supportedModels = models_extra 键 − excludes；remark 取胜出渠道值）。
4. **退役**（此时新链路已验证）：删 `models-mapping/` 目录（build_mapping/sync_models/select + SKILL + tests，职责已被 model-registry 吸收）、`build-mapping.yml`、`axonhub-admin/scripts/apply_channel_models.py` + 其测试、`data/opencode-go-models.json`、`data/goat-models.json`、`data/enriched.json`；config 清理 external_channel_lists。
5. **文档收尾**：新写 `docs/adr/0012-model-registry-pipeline.md`（数据源→产物重组、去重语义、claude 不采集+自建映射、推送线退役、CI 只采集/Agent 计算的分工；supersede 0010 与 0011 相应条款）；更新 README（数据边界表/流程图/命令）、AGENTS.md、CONTEXT.md（Allowlist/Managed channel 词条）、SECURITY.md（删 ：12 推送账号、:31 无人值守段落）、data/formula.md（候选宇宙=去重归属渠道，公式不变）、axonhub-admin/SKILL.md（删 "Unattended push" 节）；`axonhub-admin/deploy-vol-server-push.md` 改写为退役指南。

每笔提交后 `python3 -m pytest -q` 全绿。

## 4. 需要你手动做的（vol-server，我无法 SSH）

`systemctl disable --now axonhub-push-channel-models.timer && daemon-reload`；删 service/timer 单元文件、`/var/lib/axonhub-push/`、`/etc/axonhub-push/{env,password}`；停用 `ops-push` 账号。完成后我标记 deploy 文档为已退役。

## 5. 验证

- pytest 全绿（watch-pipeline 改造测试 + model-registry 新测试 + 保留的共享库/axonhub-admin 测试）。
- models_extra.json 首版抽查（两渠道数值、claude 过滤、free 字段）；plan 抽查（supportedModels、卡片来自 all_models、remark 胜出渠道）。
- 全仓 grep 无旧路径残留（探索已列清单：README/AGENTS/CONTEXT/SECURITY/两处 SKILL/契约 script 字段）。
