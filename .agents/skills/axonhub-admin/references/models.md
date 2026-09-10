# 模型卡（model cards）

本仓库规划的第二类 AxonHub 对象：每个注册表模型的卡片与备注。生成的
`data/model-plan.json`（schema 1）是**增量式**的——每模型一个条目，含 `modelID`、归属
渠道、可选 `channelAliases`、`channelPriority` association 链、`cardRef`，以及一个由
推导元数据加渠道声明终值组成的 `input`。卡片本身绝不复制进计划。

- **卡片来源分层**——首选 AxonHub 内置目录（`providersCatalog`：上游默认
  `ThinkInAIXYZ/PublicProviderConf`，每小时刷新；models 页「批量添加」从目录条目自动组装
  完整卡片）；`data/all_models.json`（models.dev 快照）只作补充源；两个来源都不覆盖的
  渠道特有 ID 走人工兜底，绝不臆造。all_models.json 仅服务于离线规划管线，且永远不是
  清单来源：它为渠道认领的模型补事实，但绝不新增模型。`cardRef` 是原始的 `vendor/model`
  键；`cardRef: null` 表示无卡模型（`card_missing`）。
- **写时组装**——`.agents/skills/model-registry/scripts/assemble_card.py` 把一条计划
  条目渲染成完整的 AxonHub 输入：经 `model_card()`（与规划共享的唯一转写映射）从被引
  卡片填充描述性 `modelCard` 字段，计划的 `cost` 与 `remark` 作为终值。无卡模型渲染
  默认卡（reasoning/toolCall false、temperature true、text 模态、零上限）。
- **渠道 cost 优先**——计划的 `cost` 从卡片出发，随后每个渠道声明字段（`input`、
  `output`、`cache_read`、`cache_write`）逐字段覆盖；渠道的 null 值保留卡片值。
- **free 归零静默价格**——`free: true` 记录是渠道声明全部零价：它未声明的成本字段以 0
  起步而非卡片的牌价；声明了的值依旧逐字段胜出。
- **低分非免费不入册**——目录候选 `arena_score` < 1500 且非 free 的，不创建实体（连
  disabled 都不建，在册即噪音且需持续复核），例：`gemini-3.5-flash-lite`（1449.01）。
  想收录先改 `data/arena.json` 的人工指派，分数过线后走正常创建流程。
- **不管理的模型类别**——图像生成与 embedding 模型（`sensenova-u1-fast`、`bge-m3`、
  `qwen3-embedding-0.6b` 等）很少变动，不在本 skill 管理范围：不建实体、不写卡片、
  不参与重建与清理；渠道侧照常服务。
- **绝不臆造**——没有卡片的模型仅从渠道认领数据规划，并报告为 `card_missing`。
- **备注**——计划从归属渠道记录重算结构化字段（`rp5h`、`usage_quota`）——缺失的报
  `missing_remark_fields`——并携带 `manual` 字段；写入方保留远端 `manual` 内容、只替换
  计算值（写入流程见 SKILL.md 的执行循环）。

每个规划模型都是 `type: chat`；图像生成端点靠记录上人工维护的 `exclude` 旗标挡在
注册表外（见 [channel.md](channel.md)），不是靠卡片规则。另有一条渠道侧的硬过滤：开
autoSync 的渠道用 `autoSyncModelPattern` 正则在同步入口处拦截，屏蔽列表维护在
`data/models_extra.json` 顶层 `blocklist` 键（渠道 → ID 列表），不在文档里罗列。
skill 运行时重算差集（渠道实时同步清单 − 授权清单，如 commandcode-goat 的 goat 节），
把差集生成为排除正则（条目按 `(^|/)ID($|[:/])` 匹配，容忍 `:free` 后缀；regexp2 语法
支持负向断言），经 `updateChannel` 写回渠道并触发重同步。授权清单内的 `-fast`/
`-highspeed` 变种（如 commandcode 的 `deepseek-v4-flash-fast`）是正价授权，不做一刀切
屏蔽；注册表创建以授权清单/计划为依据，不以易变的实时同步快照为依据（同步清单混有
订阅档位差异与瞬时条目）。
