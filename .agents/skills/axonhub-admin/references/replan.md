# 重算规划（replan）

把 watch-pipeline 快照离线重算成评审产物：`models.csv`（映射建议）与
`data/model-plan.json`（模型增量计划）。纯离线：不联网、不持凭据、不写
AxonHub，任何时刻可以本地重跑。blocklist 作为既有输入只读消费（派生类的
物化归同步渠道清单任务的物化步骤）；怀疑派生排除陈旧（分数刚变化、新速度
变种）时，先跑一次该物化再 replan。

## 输入（均为仓库内快照）

- `data/models_extra.json` — 渠道声明事实 `channels.<channel>.<model_id>`：
  `rp5h`、`usage_quota`、`cost{}`、人工维护的 `free` 旗标、goat 的 `tok_s`。
  顶层 `blocklist.<channel>`（`{id, reason}`）是唯一排除源，本任务只读；
  reason 前缀 `lowscore:`/`speed:` 为派生类、其余为人工类。
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
   写任务（模型卡更新 / Claude 模型关联，导航见 SKILL.md）；确认映射不等于
   授权写渠道或模型，确认材料相互独立。

## 注册表构造规则（评审与排障时对照；数字阈值是脚本内常量——文档解释，代码裁决）

对象侧规则的归属：映射公式与 Arena 匹配链属 Claude 模型关联任务，跨渠道
去重/变种分组/别名归一属非 Claude 模型关联任务，卡片成本与备注属模型卡
更新任务，排除物化属同步渠道清单任务——本文件不复述。

- **free 判定**——记录级 `free` 旗标权威（含 `free: false` 反覆盖），无旗标
  时 `-free` 后缀为派生默认（采集刷新的删除重插会丢记录级手工字段，默认值
  保证这类模型不被误判；缺口以 `free_flag_missing` 呈现，供维护者补旗标）。
- **Arena + rp5h 分诊**——非 free 无任何 Arena 匹配则不带分入册但不进映射
  池（`arena_missing`）；缺 `rp5h` 的非 free 模型排除
  （`rp5h_missing_excluded`，有分场景已被 lowscore 物化先行接管，本分支
  实际覆盖查无分数者），分数达到 1500 保留带评审警告
  （`rp5h_missing_review`），仅不参与映射目标挑选（记入 INELIGIBLE）。

## 产物语义

- `models.csv`——request 行保留为输入清单，其余单元格每轮重算；是映射建议
  的可审查快照，不是 AxonHub 运行时状态。
- `model-plan.json`（schema 1）——增量条目：`modelID`、归属渠道、可选
  `channelAliases`、`channelPriority` 链（按 `rp5h` 降序的实际服务渠道）、
  `cardRef`、以及推导 meta + 渠道声明终值组成的 `input`。卡片本身绝不复制
  进计划。渠道清单治理独立于规划产物（同步正则）。
- plan 是**纯目标态**：无指纹与陈旧性机制，过期整体重算；远端漂移由写任务
  的 read-before-write 在执行时发现并报告。

## 移交

- **模型卡** → 模型卡更新任务，用户确认后写入。渠道清单治理独立于本流程
  （同步正则，不消费规划产物）。
- **Claude 映射** → 用户读 `models.csv` 后选择：Claude 模型关联任务写入，
  或 AxonHub UI 手改。日常改靶直接在 UI，不进本流程。
- **Claude 全局模型** → AxonHub 内一次手工创建，永不脚本化。
