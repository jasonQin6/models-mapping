# 模型关联（associations）

本仓库治理的第三类 AxonHub 对象：模型的 `settings.associations`——决定每个请求由哪个
渠道条目服务的规则。按系列分两种制度。**Claude 请求模型**按分数映射到其他已有模型，
由规划器算入 `models.csv` 供评审；**其余所有全局模型**映射到自身，工作在渠道选择、
优先级与 free 变种合并上，约定以人工维护的方式落在 AxonHub（UI 或本 skill 的写入流程）。
本文件是共享规则手册，不是规划器输出。

## Claude 请求模型 — 按分数映射

`models.csv`（映射工作区，见 CONTEXT.md）是评审产物：`role=request` 的行是人工维护的
请求模型清单，其余单元格每轮重算。只有 `claude-*` 请求参与映射；GPT 请求按名字直通，
从不进入映射。

每个请求选得分最高的非 free 候选：

```text
match =
    0.35 * arena_score
  + 0.30 * log_rp5h
  + 0.35 * proximity
  - downgrade_penalty
  + upgrade_bonus
```

```text
arena_score = candidate_arena_score / max_candidate_arena_score
log_rp5h = log(candidate_rp5h + 1) / log(max_candidate_rp5h + 1)
proximity = 1 - abs(candidate_arena_score - request_arena_score) / max_score_diff
downgrade_penalty = 0.2 * (request_arena_score - candidate_arena_score) / max_score_diff
upgrade_bonus = 0.1
```

所有候选同分时 `proximity` 为 `1`。惩罚只在候选低于请求分数时生效，升级加成只在高于时
生效。价格与 `usage_quota` 是源元数据，不是映射得分维度；它们经模型卡计划
（见 [models.md](models.md)）进入 AxonHub。

映射顺序：

1. **公式优先**——每个请求对全部非 free 候选按上式打分。
2. **free 补全**——free 池按 Arena 分升序与按 Arena 分升序的请求配对，替换这些请求的
   公式目标（`free_fill`）：最弱的请求拿到分最低的 free 模型。

没有人工覆盖：算出的配对就是评审建议，日常改靶在评审确认表格后于 AxonHub UI 进行。
执行形态是每个请求模型恰有一条启用的 `type=model` association 指向确认的候选——这是
唯一整体替换 associations 的场景（由本 skill 仅为固定请求模型写入）。

### Arena 分数来源

分数来自排行榜、人工指派（`data/arena.json` 里 `manual: true` 的记录，采集轮次间保留）
或下面的默认规则。匹配链会剥离一组固定变种后缀（`-contributor`、`-free`、`-vl`——
`.agents/skills/model-registry/scripts/name_matching.py` 的 `MATCH_VARIANT_SUFFIXES`）：
榜单未列出的变种继承其基础模型的分数（`ling-3.0-flash-vl` ← `ling-3.0-flash`），所以一条
人工指派的基础记录覆盖所有变种。Arena ID 原样保留榜单的参数规格后缀（`qwen3.8-27b`），
带规格的渠道 ID 可直接精确匹配。

- 非 free 模型没有任何 Arena 匹配时完全不携带分数（`arena_missing`）——列入清单但
  `arena_score` 为空，永不进入映射池；连 `rp5h` 也没有则被排除
  （`rp5h_missing_excluded`）。
- `version_downgrade` 或 `prefix_match` 的命中会被拒收（`arena_borrowed_rejected`）：
  分数属于另一个模型或一个名字家族，绝不静默归给本 ID。
- free 模型剥离后缀后仍无匹配时默认 1500 并留在池里，无论分数如何都能经 free 补全触达。
- 请求模型不在榜上是阻断性错误（`request_arena_missing`），直到一条人工指派落地。

置信度：Arena 直接匹配高；变种后缀（同一模型）匹配中；free 默认低。降版本与前缀命中
不再产生任何映射目标。携带注册表之外字母尾巴的未匹配模型（比如 `-vq`）抛
`unrecognized_variant_suffix`——绝不静默打分。人工分诊三选一：为完整 ID 人工指派一条
Arena 记录；经 `data/models_extra.json` 的 `aliases` 合并拼写（见
[channel.md](channel.md)）；或当该后缀被证实是复现的家族标记时，登记进
`MATCH_VARIANT_SUFFIXES`。版本化尾巴（`-a55b`、`-0902`）永不告警。

## 非 Claude 模型 — 自映射约定

非 Claude 全局模型路由到自己：其 association 把裸 ID 模型绑定到服务它的渠道条目上。
前提：上游厂商暴露带前缀的 ID（`deepseek/deepseek-v4-flash`），而 AxonHub Model 实体用
裸 ID，且 association 按精确字符串匹配。两种修复——渠道侧 `settings.modelMappings` 与
模型侧 association 链——见 SKILL.md 的「模型 ID 陷阱」。association 类型：`channel_model`
（钉住渠道+精确 ID）、`model`（全部渠道中的精确 ID）、`regex`（全局模式）。

- **渠道选择**——规划器为每个模型给出 `channelPriority`（`data/model-plan.json`）：每个
  实际服务该 ID 的渠道，按 `rp5h` 降序排列。association 默认是按该顺序的 `channel_model`
  链——p0 主用（去重胜出渠道），后面的是回退顺序；单渠道模型退化为 p0 钉死。
  commandcode-goat ∩ opencode-go 的交集（别名归一后）就是拥有真实回退链的模型集合。
  曾经的全局 `regex` 默认（到处 `(?i)(^|/)deepseek-v4-flash$`）已退役；把模型锁定到
  特定渠道依旧是例外用法。
- **优先级配置**——优先级数字升序即回退顺序：p0 主用，p1/p2 回退。回退链是同一渠道上的
  多条 `channel_model` 规则带升序优先级。
- **free 变种合并**——一个 free 家族合并到同一个全局模型下：主规则指向基础变种，兄弟
  free 变种作为渠道内回退——`ling-3.0-flash` → ant 的 `vl`/`sante`/`fin` 于 p0/p1/p2。
- **严格回退降级**——主规则保持 p0 并带 `exclude: [{channelIds: [<回退渠道>]}]`，回退
  渠道获得 p1 `channel_model` 规则：日常流量永不碰它，429 和故障才会。
- **时段门控路由**——free 渠道上的 p0 规则包在 `when` 的 daily_time group 里，不设限的
  主池降为 p1（输入形状见 SKILL.md 的「部署怪癖」）。

写入走本 SKILL.md 的执行循环；每个形态都用
`queryModelChannelConnections(associations: $assocs)` 验证——目标渠道解析出预期
`actualModel` 且 `source: mapping` 或 `direct` 即完成。
