# 重算规划（replan）

把 watch-pipeline 快照离线重算成三份评审产物：`models.csv`（映射建议）、
`data/channel-plan.json` 与 `data/model-plan.json`（双 plan）。纯离线：不联网、
不持凭据、不写 AxonHub，任何时刻可以本地重跑。

## 输入（均为仓库内快照）

- `data/models_extra.json` — 渠道声明事实 `channels.<channel>.<model_id>`：
  `rp5h`、`usage_quota`、`cost{}`、人工维护的 `free: true`、goat 的 `tok_s`；
  记录上人工维护的 `exclude` 原因把模型挡在注册表外。
- `data/all_models.json` — models.dev 平铺 `vendor/model` 目录；唯一卡片来源
  （永远不是清单来源：只为渠道认领的模型补事实，绝不新增模型）。
- `data/arena.json` — Arena 分数（榜单、`manual: true` 人工指派）。
- `models.csv` — 映射工作区；`role=request` 行是人工维护的请求模型清单，
  也是唯一被读回的单元格（加行/删行即增减请求模型；GPT 行按名透传，报为
  ignored，从不进入映射）。

## 流程

1. 跑规划器（在仓库根）：
   ```bash
   python3 .agents/skills/axonhub-admin/scripts/models_mapping.py \
     --csv models.csv \
     --fail-on-errors
   ```
2. 报告分类：
   - **阻断错误**（`--fail-on-errors` 下退出非零，产物仅供检查）：请求模型
     不在 Arena 榜上且无人工指派（`request_arena_missing`）、输入 schema 漂移。
   - **警告**（不阻断，必须向用户披露）：`card_missing`、`arena_missing`/
     `arena_defaulted`/`arena_borrowed_rejected`、`rp5h_missing_review`、
     `variant_superseded`、`duplicate_model_across_sources`、
     `free_default_filled`、`missing_remark_fields`。arena/mapping 警告在
     stderr，其余随其解释的产物走。
3. （可选）单模型终态预览：
   ```bash
   python3 .agents/skills/axonhub-admin/scripts/assemble_card.py --id <modelID>
   ```
4. 呈报评审：`models.csv` 变更摘要 + 双 plan 差异概览。用户确认后才进入
   对应写任务（[channel-sync.md](channel-sync.md) / [model-sync.md](model-sync.md) /
   [mapping-apply.md](mapping-apply.md)）；确认映射不等于授权写渠道或模型，
   确认材料相互独立。

## 规划规则（评审与排障时对照；数字阈值是脚本内常量——文档解释，代码裁决）

- **别名归一**——`models_extra.json` 顶层人工维护的 `aliases` 映射（如
  `tencent-hy3` → `hy3`）合并跨渠道拼写。渠道清单保留原生 ID；注册表与
  `plan.models[]` 用规范 ID，`channelAliases` 描述各渠道路由暴露。
- **排除**——速度营销 ID（`-fast`/`-highspeed`）在规划期判定排除；记录级
  `exclude` 原因同样挡在清单外（字段级合并保证跨采集轮次保留）。
- **跨渠道去重**——每个 ID 一个胜出渠道：`rp5h` 最高者胜（null 输给有值，
  平局取字母序靠前渠道），每次裁决报告 `duplicate_model_across_sources`。
- **变种分组**——同一基础模型内 `-free` 压过 `-contributor` 压过原始版；
  被取代变种以 `variant_superseded` 离场。
- **free 补全**——free 是声明的渠道事实（记录级 `free: true`），与 ID 拼写
  无关。所属渠道内 free 模型的 `rp5h` 从该渠道最大非 free `rp5h` 重新推导
  （无非 free 基准回退 1000）；缺失 `usage_quota` 补 60（`free_default_filled`）。
- **卡片与成本**——卡片字段只来自 `all_models.json`（`cardRef` 为原始
  `vendor/model` 键；无卡即 `cardRef: null` + `card_missing`，绝不臆造）。
  计划 `cost` 从卡片出发，渠道声明字段（`input`/`output`/`cache_read`/
  `cache_write`）逐字段覆盖，渠道 null 保留卡片值；`free: true` 视为渠道
  声明全部零价——未声明的成本字段以 0 起步而非牌价。
- **Arena + rp5h 分诊**——只有直接命中与同模型变种后缀命中计分
  （`arena_borrowed_rejected` 拒收 `version_downgrade`/`prefix_match`）；
  非 free 无任何 Arena 匹配则不带分入册但不进映射池（`arena_missing`），
  free 默认 1500 留在池里（`arena_defaulted`）；缺 `rp5h` 的非 free 模型
  Arena 分低于 1500 排除（`rp5h_missing_excluded`），达到 1500 保留带评审
  警告（`rp5h_missing_review`），仅不参与映射目标挑选。
- **Claude 映射公式**——每个请求对全部非 free 候选打分，取最高：
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
  高于时生效。价格与 `usage_quota` 是源元数据，不是映射维度。映射顺序：
  公式优先 → free 补全（free 池按 Arena 分升序配对 Arena 分升序的请求，
  `free_fill`：最弱请求拿分最低的 free 模型）。没有人工覆盖：算出的配对
  就是评审建议。
- **Arena 匹配链与置信度**——匹配链剥离固定变种后缀（`-contributor`、
  `-free`、`-vl`，`scripts/name_matching.py` 的 `MATCH_VARIANT_SUFFIXES`）：
  榜单未列的变种继承基础模型分数（`ling-3.0-flash-vl` ← `ling-3.0-flash`），
  所以一条人工指派的基础记录覆盖所有变种。Arena ID 原样保留榜单参数规格
  后缀（`qwen3.8-27b`），带规格的渠道 ID 可直接精确匹配。置信度：直接
  匹配高、变种后缀中、free 默认低。带注册表外字母尾巴的未匹配 ID（如
  `-vq`）抛 `unrecognized_variant_suffix`，绝不静默打分——人工三选一：
  为完整 ID 人工指派 Arena 记录；经 `aliases` 合并拼写；或后缀被证实为
  复现家族标记时登记进 `MATCH_VARIANT_SUFFIXES`。版本化尾巴（`-a55b`、
  `-0902`）永不告警。
- **备注计算**——计划从归属渠道记录重算结构化字段（`rp5h`、`usage_quota`），
  缺失报 `missing_remark_fields`，并携带 `manual` 字段（写入侧保留远端
  `manual` 内容、只替换计算值，见 [model-sync.md](model-sync.md)）。

## 产物语义

- `models.csv`——request 行保留为输入清单，其余单元格每轮重算；是映射建议
  的可审查快照，不是 AxonHub 运行时状态。
- `channel-plan.json`（schema 1）——每渠道精确、排序的原生 ID `supportedModels`
  期望态（按 `models_extra.json` 节名键）。
- `model-plan.json`（schema 1）——增量条目：`modelID`、归属渠道、可选
  `channelAliases`、`channelPriority` 链（按 `rp5h` 降序的实际服务渠道）、
  `cardRef`、以及推导 meta + 渠道声明终值组成的 `input`。卡片本身绝不复制
  进计划。
- plan 是**纯目标态**：无指纹与陈旧性机制，过期整体重算；远端漂移由写任务
  的 read-before-write 在执行时发现并报告。

## 移交

- **模型卡 + 渠道 `supportedModels`** → [channel-sync.md](channel-sync.md) /
  [model-sync.md](model-sync.md)，用户确认后写入。
- **Claude 映射** → 用户读 `models.csv` 后选择：[mapping-apply.md](mapping-apply.md)
  写入，或 AxonHub UI 手改。日常改靶直接在 UI，不进本流程。
- **Claude 全局模型** → AxonHub 内一次手工创建，永不脚本化。
