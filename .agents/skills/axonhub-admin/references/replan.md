# 重算规划（replan）

把 watch-pipeline 快照离线重算成评审产物：`models.csv`（映射建议）、
`data/model-plan.json`（模型增量计划），并把派生排除物化进
`data/models_extra.json` 的 `blocklist`（`speed:`/`lowscore:` 两类整体重建，
人工类保留）。纯离线：不联网、不持凭据、不写 AxonHub，任何时刻可以本地
重跑。

## 输入（均为仓库内快照）

- `data/models_extra.json` — 渠道声明事实 `channels.<channel>.<model_id>`：
  `rp5h`、`usage_quota`、`cost{}`、人工维护的 `free` 旗标、goat 的 `tok_s`。
  顶层 `blocklist.<channel>`（`{id, reason}`）是**唯一排除源**：`speed:`/
  `lowscore:` 两类由本流程每次运行物化重建，`manual`/`tier`/`retired` 等
  人工类原样保留。
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
   - **警告**（不阻断，必须向用户披露）：`manual_excluded`（reason 前缀
     区分：`lowscore:` 派生低分、`speed:` 派生速度营销、其余人工类）、
     `card_missing`、`arena_missing`/`arena_defaulted`/`arena_borrowed_rejected`、
     `rp5h_missing_review`、`variant_superseded`、
     `duplicate_model_across_sources`、`free_default_filled`、
     `free_flag_missing`、`missing_remark_fields`。警告全部走 stderr 摘要；
     仅卡片/备注类（`card_missing`、`missing_remark_fields`）随 model-plan
     产物携带。
3. （可选）单模型终态预览：
   ```bash
   python3 .agents/skills/axonhub-admin/scripts/assemble_card.py --id <modelID>
   ```
4. 呈报评审：`models.csv` 变更摘要 + plan 差异概览。用户确认后才进入对应
   写任务（[model-card-update.md](model-card-update.md) / [claude-模型关联.md](claude-模型关联.md)）；
   确认映射不等于授权写渠道或模型，确认材料相互独立。

## 规划规则（评审与排障时对照；数字阈值是脚本内常量——文档解释，代码裁决）

裁决规则的解释按对象归档：跨渠道去重与变种分组见
[非Claude模型关联.md](非Claude模型关联.md)，free 池默认值与映射公式见
[claude-模型关联.md](claude-模型关联.md)，卡片与成本终值见
[model-card-update.md](model-card-update.md)——本文件不复述。

- **排除 = blocklist 物化（单一机制）**——所有排除都落在
  `blocklist.<channel>`（`{id, reason}`，完整 ID 或剥厂商前缀裸 ID、大小写
  不敏感），dedupe 逐记录查它。规划器拥有两个派生类并在每次运行**整体
  重建**：`speed:`（`-fast`/`-highspeed` 速度营销变种）、`lowscore:`
  （arena_score < 1500 且非 free；查无分数不适用，走 `arena_missing` 分诊；
  想收录先改 `data/arena.json` 的人工指派，分数过线后自然回归）。人工类
  （`manual`/`tier`/`retired`/…）原样保留，与派生条目冲突时人工优先；
  陈旧的派生条目（id 已不在节里或分数回升）自动消失。blocklist 同时是
  渠道同步正则的唯一来源（[channel-sync.md](channel-sync.md)），与写侧
  "低分非免费不入册"（[model-card-update.md](model-card-update.md)）三层同线。
- **别名归一**——`models_extra.json` 顶层人工维护的 `aliases` 映射（如
  `tencent-hy3` → `hy3`）合并跨渠道拼写。注册表与 `plan.models[]` 用规范
  ID，`channelAliases` 描述各渠道路由暴露。
- **free 判定**——记录级 `free` 旗标权威（含 `free: false` 反覆盖），无旗标
  时 `-free` 后缀为派生默认（采集刷新的删除重插会丢记录级手工字段，默认值
  保证这类模型不被误判；缺口以 `free_flag_missing` 呈现，供维护者补旗标）。
  free 模型的默认补全（rp5h 推导、quota 60、默认 1500 分）见
  [claude-模型关联.md](claude-模型关联.md)。
- **Arena + rp5h 分诊**——只有直接命中与同模型变种后缀命中计分
  （`arena_borrowed_rejected` 拒收 `version_downgrade`/`prefix_match`）；
  非 free 无任何 Arena 匹配则不带分入册但不进映射池（`arena_missing`），
  free 默认 1500 留在池里（`arena_defaulted`）；缺 `rp5h` 的非 free 模型
  排除（`rp5h_missing_excluded`，有分场景已被 lowscore 物化先行接管，本
  分支实际覆盖查无分数者），分数达到 1500 保留带评审警告
  （`rp5h_missing_review`），仅不参与映射目标挑选。
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
  `manual` 内容、只替换计算值，见 [model-card-update.md](model-card-update.md)）。

## 产物语义

- `models.csv`——request 行保留为输入清单，其余单元格每轮重算；是映射建议
  的可审查快照，不是 AxonHub 运行时状态。
- `model-plan.json`（schema 1）——增量条目：`modelID`、归属渠道、可选
  `channelAliases`、`channelPriority` 链（按 `rp5h` 降序的实际服务渠道）、
  `cardRef`、以及推导 meta + 渠道声明终值组成的 `input`。卡片本身绝不复制
  进计划。渠道清单治理独立于规划产物（channel-sync 的同步正则）。
- plan 是**纯目标态**：无指纹与陈旧性机制，过期整体重算；远端漂移由写任务
  的 read-before-write 在执行时发现并报告。

## 移交

- **模型卡** → [model-card-update.md](model-card-update.md)，用户确认后写入。渠道清单治理
  独立于本流程（[channel-sync.md](channel-sync.md) 的同步正则，不消费规划产物）。
- **Claude 映射** → 用户读 `models.csv` 后选择：[claude-模型关联.md](claude-模型关联.md)
  写入，或 AxonHub UI 手改。日常改靶直接在 UI，不进本流程。
- **Claude 全局模型** → AxonHub 内一次手工创建，永不脚本化。
