# 同步渠道清单（channel-sync）

主路径：开 autoSync + 正则屏蔽。`supportedModels` = 上游同步清单 − 屏蔽集，由
AxonHub 每小时同步自动维护；本项目只治理屏蔽正则。

适用：commandcode-goat（订阅差集稳定）；opencode-go 差集为零时直接开。
例外：`ant`、`sensenova`（embedding/作图，低频）同步保持关，清单人工维护。

## 屏蔽规则

1. **后缀/前缀**——`-fast`、`-highspeed` 结尾的速度营销变种；`claude-*` 前缀
   （Claude 由自建全局模型供给）。
2. **低分**——arena_score < 1500 且非 free（free = 渠道记录 `free: true` 或
   `-free` 后缀）。分数直接取自数据源 `data/arena.json`（`manual: true` 人工
   指派优先），渠道 ID 与榜单记录用本 skill `scripts/name_matching.py` 的
   匹配链连接（剥变种后缀），不经 models.csv/plan 中转。查无分数时先主动
   刷新快照（本流程授权的本地采集例外，`manual` 记录保留）：
   ```bash
   python3 .agents/skills/watch-pipeline/scripts/watch_arena.py \
     --top-n 0 --output data/arena.json
   ```
   刷新后仍缺则人工指派（manual）或保留呈报。
3. **订阅差集/人工下架**——`data/models_extra.json` 顶层
   `blocklist.<channel>`，条目为 `{id, reason}`：`tier` = 订阅档位差（稳定），
   `manual` = 人工下架（如上游不提供的授权 id）。

## 流程

1. 算屏蔽集：对已知清单面（渠道 `supportedModels` 现状 ∪ 现有正则已挡条目）
   逐 id 过规则 1–3。**常规刷新不抓上游**——autoSync 会把上游变化自动带进来，
   命中后缀/前缀规则的自动被正则挡住。仅在需要核对上游原始清单时，用只读
   查询 `fetchModels(input: { channelType, baseURL, channelID })` 作参考
   （`baseURL` 必填、从渠道查询取；返回 id 可能带 `厂商/` 前缀与 `:free`
   后缀，比对时归一化）。
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
