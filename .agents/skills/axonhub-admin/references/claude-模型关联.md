# Claude 模型关联

把 `models.csv` 评审确认的映射写入 AxonHub：每个请求模型一条指向确认候选的
关联路由。前置：`models.csv` 的映射建议已经 replan 评审且用户对映射表明确
确认；确认映射不等于授权写渠道清单或模型卡（确认材料相互独立）。

## 建议计算（models.csv 的 mapping 列怎么来的）

只有 `claude-*` 请求参与映射；GPT 请求按名字直通，从不进入映射。映射顺序：
公式优先 → free 补全。

- **free 池默认值**（`free_default_filled` / `arena_defaulted`）——free 补全
  的配对依赖两组规划期默认值，自动补齐：free 模型的 `rp5h` 从所属渠道最大
  的非 free `rp5h` 重新推导（渠道无非 free 基准时回退 1000）；缺失的
  `usage_quota` 补 60。剥离变种后缀后仍无 Arena 匹配的 free 模型默认
  1500 分并留在池里，保证 free 补全永远有供给。
- **映射公式**——每个请求对全部非 free 候选打分，取最高：
  ```text
  match =
      0.35 * arena_score
    + 0.30 * log_rp5h
    + 0.35 * proximity
    - downgrade_penalty
    + upgrade_bonus

  arena_score      = candidate_arena_score / max_candidate_arena_score
  log_rp5h         = log(candidate_rp5h + 1) / log(max_candidate_rp5h + 1)
  proximity        = 1 - abs(candidate_arena_score - request_arena_score) / max_score_diff
  downgrade_penalty = 0.2 * (request_arena_score - candidate_arena_score) / max_score_diff
  upgrade_bonus    = 0.1
  ```
  全部候选同分时 `proximity` 为 1；惩罚只在候选低于请求分时生效，加成只在
  高于时生效。价格与 `usage_quota` 是源元数据，不是映射维度。
- **free 补全**（`free_fill`）——free 池按 Arena 分升序与按 Arena 分升序的
  请求配对，替换这些请求的公式目标：最弱请求拿分最低的 free 模型。没有
  人工覆盖：算出的配对就是评审建议。
- **Arena 匹配链与置信度**——匹配链剥离固定变种后缀（`-contributor`、
  `-free`、`-vl`，`scripts/name_matching.py` 的 `MATCH_VARIANT_SUFFIXES`）：
  榜单未列的变种继承基础模型分数（`ling-3.0-flash-vl` ← `ling-3.0-flash`），
  所以一条人工指派的基础记录覆盖所有变种。Arena ID 原样保留榜单参数规格
  后缀（`qwen3.8-27b`），带规格的渠道 ID 可直接精确匹配。置信度：直接匹配
  高、变种后缀中、free 默认低。`version_downgrade`/`prefix_match` 的命中被
  拒收（`arena_borrowed_rejected`），绝不静默归分；非 free 无任何 Arena
  匹配则不带分入册但不进映射池（`arena_missing`）。带注册表外字母尾巴的
  未匹配 ID（如 `-vq`）抛 `unrecognized_variant_suffix`，绝不静默打分——
  人工三选一：为完整 ID 人工指派 Arena 记录；经 `aliases` 合并拼写；或
  后缀被证实为复现家族标记时登记进 `MATCH_VARIANT_SUFFIXES`。版本化尾巴
  （`-a55b`、`-0902`）永不告警。请求模型不在榜上是阻断性错误
  （`request_arena_missing`），直到一条人工指派落地。

## 流程

1. Token（见 [SKILL.md](../SKILL.md) 的 Token 节）。
2. 对账读：`models(first:100)` 含 `settings { associations { … } }`；确认每个
   请求模型已存在且为全局实体。
3. 工作清单：每个请求模型 → 恰好一条**启用的** `type=model` association 指向
   确认的候选。这是唯一整体替换 `settings.associations` 的场景（由本 skill
   仅为固定请求模型写入）；其他模型的既有 associations 不碰。
4. 确认门：展示"请求 → 候选"清单，一次确认。
5. 写入：逐条 `updateModel`，`settings` 按形状嵌套在 `-v` 变量里整体回写。
6. 验证：`queryModelChannelConnections(associations: $assocs)`——目标渠道
   解析出预期 `actualModel` 且 `source: mapping` 或 `direct` 即完成。

## 规则

- 请求模型必须预先存在于 AxonHub（映射流程不创建/删除它）；Claude 全局
  模型一次手工创建，永不脚本化。
- 没有人工覆盖层：写入的就是评审确认的配对；日常改靶直接在 AxonHub UI
  进行，不进本流程。
