# 模型卡（model cards）

本仓库规划的第二类 AxonHub 对象：每个注册表模型的卡片与备注。生成的
`data/model-plan.json`（schema 1）是**增量式**的——每模型一个条目，含 `modelID`、归属
渠道、可选 `channelAliases`、`channelPriority` association 链、`cardRef`，以及一个由
推导元数据加渠道声明终值组成的 `input`。卡片本身绝不复制进计划。

- **单一卡片来源**——`data/all_models.json`（models.dev 快照）是唯一的卡片来源，永远
  不是清单来源：它为渠道认领的模型补事实，但绝不新增模型。`cardRef` 是原始的
  `vendor/model` 键；`cardRef: null` 表示无卡模型（`card_missing`）。
- **写时组装**——`.agents/skills/model-registry/scripts/assemble_card.py` 把一条计划
  条目渲染成完整的 AxonHub 输入：经 `model_card()`（与规划共享的唯一转写映射）从被引
  卡片填充描述性 `modelCard` 字段，计划的 `cost` 与 `remark` 作为终值。无卡模型渲染
  默认卡（reasoning/toolCall false、temperature true、text 模态、零上限）。
- **渠道 cost 优先**——计划的 `cost` 从卡片出发，随后每个渠道声明字段（`input`、
  `output`、`cache_read`、`cache_write`）逐字段覆盖；渠道的 null 值保留卡片值。
- **free 归零静默价格**——`free: true` 记录是渠道声明全部零价：它未声明的成本字段以 0
  起步而非卡片的牌价；声明了的值依旧逐字段胜出。
- **绝不臆造**——没有卡片的模型仅从渠道认领数据规划，并报告为 `card_missing`。
- **备注**——计划从归属渠道记录重算结构化字段（`rp5h`、`usage_quota`）——缺失的报
  `missing_remark_fields`——并携带 `manual` 字段；写入方保留远端 `manual` 内容、只替换
  计算值（写入流程见 SKILL.md 的执行循环）。

每个规划模型都是 `type: chat`；图像生成端点靠记录上人工维护的 `exclude` 旗标挡在
注册表外（见 [channel.md](channel.md)），不是靠卡片规则。
