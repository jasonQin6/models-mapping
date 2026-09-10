# 同步渠道清单（channel-sync）

主路径：开 autoSync + 正则屏蔽。`supportedModels` = 上游同步清单 − 屏蔽集，由
AxonHub 每小时同步自动维护；本项目只治理屏蔽正则。

适用：commandcode-goat（订阅差集稳定）；opencode-go 差集为零时直接开。
例外：`ant`、`sensenova`（embedding/作图，低频）同步保持关，清单人工维护。

## 屏蔽规则

1. **后缀/前缀**——`-fast`、`-highspeed` 结尾的速度营销变种；`claude-*` 前缀
   （Claude 由自建全局模型供给）。
2. **低分**——arena_score < 1500 且非 free；查无分数的保留并呈报。
3. **订阅差集**——`data/models_extra.json` 顶层 `blocklist.<channel>`（人工
   维护的稳定差集，如 goat 的 12 条订阅档位差）。

## 流程

1. 算屏蔽集：上游同步清单逐 id 过规则 1–3。
2. 生成允许正则：屏蔽条目按 `(^|/)ID($|[:/])` 匹配（容忍 `:free` 后缀，id 中
   `.` 转义；regexp2 支持负向断言）。示例形状：
   ```text
   ^(?!.*-(?:fast|highspeed)$)(?!.*(?:^|/)claude-)(?!.*(?:^|/)(?:gpt-5\.5|google/gemini-3\.5-flash-lite)(?:$|[:/])).*$
   ```
3. 确认屏蔽集与正则（一次确认）。
4. 一次 `updateChannel` 同时写 `autoSyncSupportedModels: true` 与
   `autoSyncModelPattern`（`-v` 按形状嵌套变量）。
5. 回读该渠道：`supportedModels` = 同步清单 − 屏蔽集即完成；时滞来自上游
   重同步周期，属正常。

本任务可短路 SKILL.md 的执行循环——单渠道两字段更新，无需全量对账仪式；
差集变化（新订阅档位、新速度变种）时重跑本流程刷新正则即可。
