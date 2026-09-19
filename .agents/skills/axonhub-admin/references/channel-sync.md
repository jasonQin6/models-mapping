# 同步渠道清单（channel-sync）

主路径：开 autoSync + 正则过滤。`supportedModels` = 上游同步清单 ∩ 过滤正则，
由 AxonHub 每小时同步自动维护；本项目只治理这份正则。全部 autoSync 渠道统一
用**屏蔽枚举**（`blocklist.<channel>` 的纯枚举负向排除）：上游新增 id 立即
放行、进清单后再判。放行清单（正向枚举 `models_extra.json` 里该渠道的模型
id）方案已否决——它会把上游新增模型
挡到文档收录为止；不要再提议。

例外：`ant`、`sensenova`（embedding/作图，低频）同步保持关，清单人工维护
（脚本仍会为其 blocklist 节生成正则，仅供参考，不写入）。

## 屏蔽来源：blocklist 是唯一真相源

渠道同步正则就是 `data/blocklist.json` 的 `blocklist.<channel>` 的纯
枚举——除此之外没有规则式屏蔽。条目为 `{id, reason}`，reason 前缀区分归属：

| reason 前缀 | 归属 | 含义 |
| --- | --- | --- |
| `lowscore:` | 脚本重建 | arena < 1500 且非 free |
| `speed:` | 脚本重建 | `-fast`/`-highspeed` 速度营销变种 |
| `superseded:` | 脚本重建 | 变体取代：组内 `-free` 压过 `-contributor` 压过普通版（与 `registry.py` 的 `variant_supersessions` 同一裁决），落败变体仍被渠道暴露即屏蔽 |
| `manual` | 人工 | 人工下架（如上游不提供的授权 id） |
| `tier` | 人工 | 订阅档位差（稳定差集） |
| `retired` | 人工 | 已淘汰（版本迭代等判断题，建议来自 ② 的 `retire_suggested` 呈报；opencode-go 的「上游 `/v1/models` 有、go.mdx 无」差集淘汰也在此类人工补录，上游彻底下线后条目即失去作用，可清理） |

三类自动条目（`speed:`/`lowscore:`/`superseded:`）由脚本从当前快照**整体重建**（陈旧派生条目自动消失，分数回升的
模型自动回归），人工三类手工保留、与派生冲突时人工优先。**本任务不做规则式
推导**——规则排除同样以 blocklist 条目落地；发现该挡的没挡且重建后仍无
条目，属于数据/规则缺口，停手呈报。

superseded 屏蔽有一个连带效应：它跟随渠道页面里的变体组成自动派生——当胜出
变体从页面上消失（如 free 变体被下架），变体组解体，落败变体的屏蔽随重建自动
消失，落败变体可能从其它渠道的文档记录重回候选清单（实例：longcat-2.0-free 转
收费下架后，longcat-2.0 借 opencode-go 文档记录重回）。若用户裁决仍要挡，按
当次裁决补 `manual` 条目。

人工条目生命周期：脚本重建的条目随快照自愈，人工类会积累死条目。每次重建时，脚本把
「id 已不在该渠道页面（`models_extra.json` 该渠道的键集合）」的人工条目呈报
到 stderr 作为候选；**裁定用上游对账**——`syncChannelModels(channelID,
pattern: "")` 一次性以空正则同步并返回原始清单（不污染存储的
`autoSyncModelPattern`，随后再不带 pattern 跑一次即恢复过滤视图）：条目 id 在
原始清单里 = 上游仍提供，条目继续生效；不在 = 上游已停。但 `models_extra.json`
里该渠道仍列着这个 id 时，条目还要负责把它挡在候选清单外——只有「页面和原始
清单都没有它」才可清理。

## 流程

1. 跑脚本（在仓库根，纯离线、幂等可重跑；先重建屏蔽条目后生成）：
   ```bash
   python3 .agents/skills/axonhub-admin/scripts/channel_sync.py [--channel <name>]
   ```
   重建结果原子写回 `data/blocklist.json`（摘要走 stderr），每渠道允许正则以
   JSON 打到 stdout。正则形状：`(?i)` 前缀必需（同步 ID 是带厂商前缀的混合
   大小写，小写模式静默漏挡）；固定 `claude-*` 前缀屏蔽（Claude 由自建全局
   模型供给，永不采集）；条目按剥厂商前缀的裸 ID、`(^|/)ID($|[:/])` 匹配
   （元字符转义；regexp2 方言，与 Python `re` 行为一致）。实测形状
   （goat，2026-09-11）：
   ```text
   (?i)^(?!.*(^|/)claude-)(?!.*(^|/)(?:deepseek\-v4\-flash\-fast|…|tencent\-hy3)(?:$|[:/])).*$
   ```
2. 确认屏蔽集与正则（一次确认），**并与渠道当前存储的 `autoSyncModelPattern`
   比对**——渠道侧可能落后于本次生成结果（实例：两渠道的存储正则曾长期漏挡
   已在册条目，靠这次比对才暴露），不一致就进入第 3 步回写，一致则跳过。
3. 一次 `updateChannel` 同时写 `autoSyncSupportedModels: true` 与
   `autoSyncModelPattern`（`-v` 按形状嵌套变量）。
4. 回读该渠道：`supportedModels` = 同步清单 − blocklist 即完成；时滞来自
   上游重同步周期，属正常。渠道对账读要拆两条查再按 id 拼接（`tags` 与
   `settings` 组合查询会报错）：
   `queryChannels(input:{first:50}) { edges { node { id name type status supportedModels manualModels autoSyncSupportedModels … } } }`
   与 `queryChannels(input:{first:50}) { edges { node { id settings { modelMappings { from to } } } } }`

本任务可短路 SKILL.md 的执行循环——单渠道两字段更新，无需全量对账仪式；
blocklist 变化（重建刷新、新增人工条目）后重跑脚本刷新正则即可。
