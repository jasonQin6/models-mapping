# goat 渠道（已退役）

commandcode-goat 是 Command Code GOAT 订阅渠道（commandcode.ai 的 plan 页），
曾由本仓采集：`watch_goat.py` 抓 plan 页 HTML 的三张表（模型/请求配额/月度
credits），落 `channels.commandcode-goat` 节，blocklist 曾有 40 条。2026-09-30
订阅取消、`testChannel` 实测 insufficient credits，渠道退役；采集 job、脚本、
测试与 fixture 已删除（可从 git 历史找回），快照节与 blocklist 条目已移除。
本文只保留它留下的、与具体渠道无关也成立的教训与恢复路径；当前渠道清单见
watch-pipeline SKILL.md 的 Channels 表。

## 路由开关教训

- `autoTrimedModelPrefixes`（前缀全量提取）+ `lowercaseModelId` 使带前缀条目
  派生出裸小写路由键（source=auto_trim）：association 钉规范 ID 即命中。
- `hideOriginalModels` **保持关闭**——direct 键与 trim 键并存是同一模型的两个
  入口别名（非冲突）；开着它裸拼写条目反而失去路由键（gpt-5.6-sol/luna 断链
  先例）。
- 写侧对应规则（钉规范 ID 为默认、例外钉原生拼写）已收敛进 SKILL.md 的
  模型 ID 陷阱与非 Claude 模型关联文档，本篇不重复。

## 处置记录（2026-09-30，留壳禁用路线）

渠道正则全排除禁供给 → 全部回退链移除该渠道条目 → ④ 重算映射（候选池剔除，
free 池与公式档重排）→ goat 独有死卡 9 张删除（ling-3.1-free / pixel-canary /
space-bunny-alpha / laguna / ling-sante / gemini-3.8-flash / nemotron /
qwen3.8-27b / qwen3.8-0902）→ 快照节与 blocklist 40 条移除、采集 job 下线。
渠道本体当时保留 enabled 留壳（与 ant 的 `deleteChannel` 硬删对照，见
SKILL.md 的渠道退役处置链）。

## 恢复订阅

重建 watch-pipeline 的 goat 采集 job（脚本自 git 历史恢复）、恢复快照节与
blocklist 条目（如有）、重新走 ①②③④，并按 field contract 重新验收解析器
（plan 页三张表结构可能已漂移）。
