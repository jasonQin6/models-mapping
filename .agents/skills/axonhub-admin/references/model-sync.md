# 同步模型（model-sync）

把 `data/model-plan.json` 增量应用到 AxonHub：建缺失实体、更新卡片/成本/
备注、启用、清理。整体重建场景走 [rebuild.md](rebuild.md)。

## 流程

1. Token（见 [SKILL.md](../SKILL.md) 的 Token 节）。
2. 对账读：`models(first:100)`，卡片字段取全（reasoning/toolCall/temperature/
   modalities/vision/cost/limit/knowledge/releaseDate/lastUpdated），为整体
   回写做准备。
3. 工作清单 = plan × 线上差集，顺序规则：删除条件按"成本已更新"的状态评估
   （先成本后删除判定）；满足删除条件的 ID 直接从创建清单剔除（不要建了又删）；
   已在删除清单里的对象跳过成本/备注更新。
4. 确认门：展示工作清单，一次确认。
5. 创建：写时组装完整输入——
   ```bash
   python3 .agents/skills/axonhub-admin/scripts/assemble_card.py --id <modelID>
   ```
   （`model_card()` 与规划共享的唯一转写映射；`cardRef: null` 渲染默认卡：
   reasoning/toolCall false、temperature true、text 模态、零上限）——
   `bulkCreateModels(inputs: […])` 一次建齐。
6. 更新：逐条 `updateModel` 循环（修改没有 bulk mutation；npx 每次约 2s 启动
   开销，可接受）。`modelCard` 输入是全量替换——读全卡、只改目标字段、整体
   回写；只传 `cost` 会把能力位清成零值。
7. 建后启用：`createModel` 与 Web UI 建出的模型都是 disabled，用
   `bulkEnableModels(ids)`（或 `updateModelStatus`）翻转。
8. 删除纪律：删除前查关联引用——被任何其他模型的 association 规则引用的
   模型保留并报告，不删。`deleteModel(id)` 是硬删（可重建同名）；
   `bulkDeleteModels(ids)` 是软删（返回 true 但不清理，且阻塞同名重建）。
   以"随后的 createModel 成功"验证清除，不信返回值。
9. 回读验证：逐项回读 + 全量对账计数（回读是唯一权威信号，CLI 输出只是线索）。

## 卡片与备注规则

- **卡片来源分层**：① AxonHub 内置目录（`providersCatalog`，上游默认
  `ThinkInAIXYZ/PublicProviderConf` 每小时刷新，拉取失败回退二进制内嵌快照；
  models 页「批量添加」从目录条目自动组装完整卡片）→ ② `data/all_models.json`
  （models.dev 快照，离线规划管线用）→ ③ 人工兜底（渠道特有/最新 ID，两个源
  都常缺，绝不臆造）。
- **低分非免费不入册**：`arena_score` < 1500 且非 free 的候选由规划器在
  排除链首位直接挡下（`lowscore_excluded`，见 [replan.md](replan.md)），
  不会出现在计划里；本流程只是不再为其创建实体的镜像约束（连 disabled 都
  不建，在册即噪音且需持续复核）。想收录先改 `data/arena.json` 的人工
  指派，分数过线后自然回归。
- **不管理的模型类别**：图像生成与 embedding 模型（`sensenova-u1-fast`、
  `bge-m3`、`qwen3-embedding-0.6b` 等）很少变动，不在管理范围：不建实体、
  不写卡片、不参与重建与清理；渠道侧照常服务。
- **备注**：写入保留远端 remark 的 `manual` 内容、只替换计划计算值
  （`rp5h`、`usage_quota`）。

## Payload 约定（CreateModelInput 字段映射，卡片数据优先取内置目录）

- `developer` — models.dev 厂商前缀归一化为 AxonHub 英文厂商词表：
  `zai-org`/`zhipuai` → `zai`，`meituan` → `longcat`，`moonshotai` →
  `moonshot`，`deepseek-ai` → `deepseek`。
- `icon` — 按厂商给 lobe-icons 名（DeepSeek、ChatGLM、Qwen、Moonshot、XAI、
  Hunyuan、LongCat、XiaomiMiMo、Meta、NVIDIA、Step、Gemini、OpenAI）；
  不确定就空串。
- `group` — 卡片的 `family`。
- `type` — `chat`；图像生成端点（`POST /v1/images/generations`，无图像输入、
  非 Chat Completions）是 `image_generation`，modalities `input: [text]` /
  `output: [image]`、`vision: false`，并靠 `models_extra.json` 顶层
  `blocklist` 条目排除出聊天注册表。
- `modelCard` — `reasoning: {supported, default}` ← `reasoning`；
  `toolCall` ← `tool_call`；`temperature` ← `temperature`（默认 true）；
  `vision` ← modalities.input 里的 `image`；`modalities`、`limit` 照搬；
  `cost` ← `{input, output, cacheRead: cache_read, cacheWrite: cache_write}`；
  `knowledge`、`releaseDate` ← `release_date`、`lastUpdated`（存在时）。
- `settings` — `{associations: []}`；可选策略字段（`disableDeveloperSettingsInheritance`/
  `loadBalancerStrategy`/`traceStickyMode`）未变就省略（部署怪癖，见 SKILL.md）。
- 成本终值已在计划里算好（渠道声明逐字段覆盖、free 归零，见
  [replan.md](replan.md)），payload 直接用计划 `input.cost`。
