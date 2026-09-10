# 配置非 Claude 路由（routing）

非 Claude 全局模型自映射：其 association 把裸 ID 模型绑定到服务它的渠道条目。
前提是模型 ID 陷阱——上游用厂商前缀 ID（`deepseek/deepseek-v4-flash`），Model
实体用裸 ID，association 按精确字符串匹配（详见 [SKILL.md](../SKILL.md) 的
部署怪癖）。本文件是约定手册；写入走 SKILL.md 的执行循环。

## 流程（从 `data/model-plan.json` 的 `channelPriority` 出发）

1. 取该模型的 `channelPriority`：每个实际服务该 ID 的渠道，按 `rp5h` 降序。
2. 默认形态：按该顺序的 `channel_model` 链——p0 主用（去重胜出渠道），其后是
   回退顺序；单渠道模型退化为 p0 钉死。
3. free 变种合并：一个 free 家族合并到同一全局模型下——主规则指向基础变种，
   兄弟 free 变种作为渠道内回退（`ling-3.0-flash` → ant 的 `vl`/`sante`/
   `fin` 于 p0/p1/p2）。
4. 可选强化（按需逐模型采纳，不是默认）：
   - **严格回退降级**——主规则保持 p0 并带 `exclude: [{channelIds: [<回退渠道>]}]`，
     回退渠道获得 p1 `channel_model` 规则：日常流量永不碰它，429 和故障才会。
   - **时段门控路由**——free 渠道上的 p0 规则包在 `when` 的 daily_time group
     里，不设限的主池降为 p1（输入形状见 SKILL.md 的部署怪癖）。
5. 验证：`queryModelChannelConnections(associations: $assocs)`——目标渠道解析
   出预期 `actualModel` 且 `source: mapping` 或 `direct` 即完成。

## 约定

- association 类型：`channel_model`（钉住渠道+精确 ID）、`model`（全部渠道中的
  精确 ID）、`regex`（全局模式）。曾经的全局 `regex` 默认（到处
  `(?i)(^|/)deepseek-v4-flash$`）已退役；把模型锁定到特定渠道依旧是例外用法。
- 优先级数字升序即回退顺序：p0 主用，p1/p2 回退；同一渠道上的多条
  `channel_model` 规则带升序优先级构成渠道内回退链。
- `when` 条件的根必须是 group：裸 `condition` 会被拒
  （`root when condition must be a group`）。
- association 输入的 `channelId` 用整数；GraphQL ID 是 GID
  （`gid://axonhub/Model/23`）。
