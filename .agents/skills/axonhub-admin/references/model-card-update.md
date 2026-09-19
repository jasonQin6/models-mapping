# 模型卡更新（model-card-update）

把脚本算出的目标清单（每个渠道模型应该有怎样的模型卡）增量应用到 AxonHub：
建缺失实体、更新卡片/成本/备注、启用、清理。目标清单由脚本离线计算，差集（目标清单 × 线上状态）在会话里对照得出——
没有中间产物文件。目录清空/迁移后的整体重建见文末「整体重建」。

## 流程

1. Token（见 [SKILL.md](../SKILL.md) 的 Token 节）。
2. 算目标清单（在仓库根，纯离线）：
   ```bash
   python3 .agents/skills/axonhub-admin/scripts/model_card_update.py
   ```
   stdout 是全部模型的目标清单 JSON（`modelID`、归属渠道、`cardRef`、可选
   `channelAliases`、成本/备注终值）；警告走 stderr。单模型完整写时 payload
   （引用卡片展开成 AxonHub `modelCard` 形状）：
   ```bash
   python3 .agents/skills/axonhub-admin/scripts/model_card_update.py --id <modelID>
   ```
3. 对账读：`models(first:100)`，卡片字段取全（reasoning/toolCall/temperature/
   modalities/vision/cost/limit/knowledge/releaseDate/lastUpdated），为整体
   回写做准备。
4. 工作清单 = 目标清单 × 线上差集，顺序规则：删除条件按"成本已更新"的状态评估
   （先成本后删除判定）；满足删除条件的 ID 直接从创建清单剔除（不要建了又删）；
   已在删除清单里的对象跳过成本/备注更新。对账读同时产出「线上有、目标清单无」
   的实体清单——逐个呈报并请求裁决（删/保留），不默认删除，也不等维护者自行
   发现。
5. **新候选必须逐条裁决**：创建清单里每个"线上无实体的首次出现 ID"都要单独
   呈报并询问——建卡或屏蔽（屏蔽 = 写 `manual` 条目并重跑 ①），不得并入批量
   确认。信号来自采集层：watcher 合并时会把页面新增 ID 打到 stderr
   （`new model ids on the page`）。整批重建/恢复除外，按其自身流程走。
6. 确认门：展示工作清单，一次确认。
7. 创建：`bulkCreateModels(inputs: […])` 一次建齐，每模型 payload 用 `--id`
   输出。
8. 更新：逐条 `updateModel` 循环（修改没有 bulk mutation；npx 每次约 2s 启动
   开销，可接受）。`modelCard` 输入是全量替换——读全卡、只改目标字段、整体
   回写；只传 `cost` 会把能力位清成零值。
9. 建后启用：`createModel` 与 Web UI 建出的模型都是 disabled，用
   `bulkEnableModels(ids)`（或 `updateModelStatus`）翻转。
10. 删除纪律：删除前查关联引用——被任何其他模型的 association 规则引用的
   模型保留并报告，不删。`deleteModel(id)` 是硬删（可重建同名）；
   `bulkDeleteModels(ids)` 是软删（返回 true 但不清理，且阻塞同名重建）。
   以"随后的 createModel 成功"验证清除，不信返回值。
11. 回读验证：逐项回读 + 全量对账计数（回读是唯一权威信号，CLI 输出只是线索）。

## 卡片与备注规则

- **卡片来源分层**：① AxonHub 内置目录（`providersCatalog`，上游默认
  `ThinkInAIXYZ/PublicProviderConf` 每小时刷新，拉取失败回退二进制内嵌快照；
  models 页「批量添加」从目录条目自动组装完整卡片，整体重建优先走这条路）
  → ② `data/all_models.json`（models.dev 快照，脚本 `cardRef` 与 `--id`
  组装的来源）→ ③ 人工兜底（渠道特有/最新 ID，两个源都常缺，绝不臆造）。
- **cardRef 与成本终值（card_missing 的裁决）**——目标清单的卡片字段只来自
  `all_models.json`：`cardRef` 为原始 `vendor/model` 键，无卡即 `cardRef: null`
  并报 `card_missing`（绝不臆造；写时依次尝试内置目录 → 人工兜底，都缺则
  `--id` 渲染默认卡：reasoning/toolCall false、temperature true、text 模态、
  零上限）。成本从卡片出发，渠道声明字段（`input`/`output`/`cache_read`/
  `cache_write`）逐字段覆盖，渠道 null 保留卡片值；渠道把 input/output 声明为
  零价即免费——免费模型的未声明成本字段以 0 起步而非牌价。
- **低分非免费不建卡**：`arena_score` < 1500 且非 free 的候选已在 blocklist
  重建时被排除（reason 前缀 `lowscore:`），不会出现在目标清单里；本流程是同一
  约束的写侧镜像——不为其创建实体（连 disabled 都不建，建了反而是需要长期清理的噪音）。想收录先改 `data/arena.json` 的人工指派，分数过线后自然回归。
- **不管理的模型类别**：图像生成与 embedding 模型（`sensenova-u1-fast`、
  `bge-m3`、`qwen3-embedding-0.6b` 等）很少变动，不在管理范围：不建实体、
  不写卡片、不参与重建与清理；渠道侧照常服务。
- **备注**：目标清单从归属渠道记录重算结构化字段（`rp5h`、`usage_quota`），
  缺失报 `missing_remark_fields`；写入保留远端 remark 的 `manual` 内容、
  只替换计算值。

## Payload 约定（CreateModelInput）

机械映射由脚本实现（`--id` 输出即成品：`model_card()` 是唯一转写映射，
`developer` 归一化、`icon` 词表、`modelCard` 字段映射都在
`model_card_update.py` 里，文档不复述）。此处只记录脚本覆盖不到的判断项：

- `type` 一律为 `chat`；图像生成端点（`POST /v1/images/generations`，
  无图像输入、非 Chat Completions）是 `image_generation`、modalities
  `input: [text]` / `output: [image]`、`vision: false`——这类模型靠
  `data/blocklist.json` 条目排除出聊天候选清单，正常流程遇不到；
  只有整体重建从 `providersCatalog` 建条目时按目录自身的 type 走。
- `settings` 为 `{associations: []}`（关联接线是后续独立任务）；可选策略字段
  （`disableDeveloperSettingsInheritance`/`loadBalancerStrategy`/
  `traceStickyMode`）未变就省略——多传反而可能触发瞬态校验器（见 SKILL.md
  部署怪癖）。

## 整体重建（rebuild）

目录被清空或迁移后的整体重建；单条/增量写入属上面的日常流程。调用纪律见
SKILL.md 执行循环。

1. 对账读：`models(first:100)` 从线上状态重算"剩余工作"——被中断/取消的
   运行是常态，可能已部分落库（实例：一次被取消的运行其实已落库 23 个创建
   和 4 个删除，重放会造出重复模型），绝不盲目重放。
2. 软删探测：软删除的行对 `models` 不可见但仍占着 ID，`createModel` 会报
   `model name 'x' already exists`。用 node 查询绕过软删拦截器探测：
   `node(id: "gid://axonhub/Model/<n>") { … on Model { modelID } }`，
   按 ID 升序探测可重建完整清单。
3. 清理：`deleteModel(id)` 硬删（resolver 跳过软删拦截器，可重建同名）；
   `bulkDeleteModels(ids)` 返回 `true` 但观察为不清理。每次清理以"随后的
   createModel 成功"验证，从不信返回值。
4. 优先路径：models 页「批量添加」从 `providersCatalog` 条目自动组装完整
   卡片；等价的 GraphQL 流程是查 `providersCatalog(filtered: true)`，按条目
   构造 `CreateModelInput`（实测 `gpt-5.5` 一次成型，卡片完整）。目录没有的
   ID 再用 `model_card_update.py --id` 从 `all_models.json` 组装，都没有才
   人工兜底。
5. 创建：`bulkCreateModels(inputs: […])` 一次建齐；每模型 payload 的
   `settings` 为 `{associations: []}`。
6. 建后启用：`bulkEnableModels(ids)` 翻转（建出的都是 disabled）。
7. 关联接线是建完后的独立一轮：非 Claude 自映射与请求模型路由分别属
   非 Claude 模型关联 / Claude 模型关联任务。
8. 每轮之后与线上对账（`models(first:100)`），只补真正缺失的部分。
