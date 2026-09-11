# 同步渠道清单（channel-sync）

主路径：开 autoSync + 正则屏蔽。`supportedModels` = 上游同步清单 − 屏蔽集，由
AxonHub 每小时同步自动维护；本项目只治理屏蔽正则。

适用：commandcode-goat（订阅档位差集稳定）、opencode-go（淘汰差集：上游
`/v1/models` 有、go.mdx 无的 id 视作已淘汰，落 blocklist 后由正则屏蔽）。
例外：`ant`、`sensenova`（embedding/作图，低频）同步保持关，清单人工维护。

## 屏蔽来源：blocklist 是唯一真相源

渠道同步正则就是 `data/models_extra.json` 顶层 `blocklist.<channel>` 的纯
枚举——除此之外没有规则式屏蔽。条目为 `{id, reason}`，reason 前缀区分归属：

| reason 前缀 | 归属 | 含义 |
| --- | --- | --- |
| `lowscore:` | 本流程物化 | arena < 1500 且非 free |
| `speed:` | 本流程物化 | `-fast`/`-highspeed` 速度营销变种 |
| `manual` | 人工 | 人工下架（如上游不提供的授权 id） |
| `tier` | 人工 | 订阅档位差（稳定差集） |
| `retired` | 人工 | 已淘汰（opencode-go 判定：上游 `/v1/models` 有、go.mdx 无；差集随每日刷新重核，新增淘汰 id 补进 blocklist） |

派生两类由本流程的物化步骤从当前快照**整体重建**（陈旧派生条目自动消失，
分数回升的模型自动回归），人工三类手工保留、与派生冲突时人工优先。
**渠道同步任务不做规则式推导**——派生排除同样以 blocklist 条目落地；发现
该挡的没挡且物化后仍无条目，属于数据/规则缺口，停手呈报。

## 流程

1. 物化派生类（在仓库根，纯离线快照处理）：
   ```bash
   python3 .agents/skills/axonhub-admin/scripts/models_mapping.py \
     --materialize-blocklist
   ```
   重建 `speed:`/`lowscore:` 两类并原子写回，输出各渠道条目计数。
2. 读 `blocklist.<channel>` 全部条目，补上固定的 `claude-*` 前缀屏蔽
   （Claude 由自建全局模型供给，永不采集）。
3. 生成允许正则：`(?i)` 前缀必需——上游同步 ID 是带厂商前缀的混合大小写
   （`zai-org/GLM-5`、`MiniMaxAI/MiniMax-M2.5`），小写模式静默漏挡。条目
   按取剥厂商前缀的裸 ID、`(^|/)ID($|[:/])` 匹配（id 中 `.` 转义；regexp2
   支持负向断言）。实测形状（goat，2026-09-11）：
   ```text
   (?i)^(?!.*(^|/)claude-)(?!.*(^|/)(?:deepseek-v4-flash-fast|…|tencent-hy3)(?:$|[:/])).*$
   ```
4. 确认屏蔽集与正则（一次确认）。
5. 一次 `updateChannel` 同时写 `autoSyncSupportedModels: true` 与
   `autoSyncModelPattern`（`-v` 按形状嵌套变量）。
6. 回读该渠道：`supportedModels` = 同步清单 − blocklist 即完成；时滞来自
   上游重同步周期，属正常。

本任务可短路 SKILL.md 的执行循环——单渠道两字段更新，无需全量对账仪式；
blocklist 变化（物化刷新、新增人工条目）后重跑本流程刷新正则即可。
