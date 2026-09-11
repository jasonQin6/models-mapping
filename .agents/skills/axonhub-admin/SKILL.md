---
name: axonhub-admin
description: 本仓对 AxonHub 部署（https://axon.jasonqin.site）的全链路治理：离线重算模型注册表与 Claude 映射建议（models.csv、data/model-plan.json），以及用户确认后的受控写入——渠道同步正则、模型卡与成本备注、关联路由、批量重建。凡任务涉及重算规划、评审映射表、生成/应用 plan、同步渠道或模型到 AxonHub，都先用本 skill。工具机制（token、endpoint、find/query/mutate 用法）以 axonhub-cli skill 为准；仓库外的一次性通用查询也用 axonhub-cli。
---

# AxonHub Admin

对 AxonHub 的管理操作全部走 `/admin/graphql`，鉴权用 JWT token（不是静态 key）。
本 skill 是 axonhub-cli 的 wrapper：**怎么连、怎么调**见 axonhub-cli；**做什么、
按什么纪律做**见本文与 `references/` 的任务流程。

## 定位与分层

- 两阶段：**离线规划**（只消费仓库内快照，不联网、不持凭据、可随时重跑）→
  **受控写入**（交互会话、凭据、逐次确认）。
- 采集只发生在 watch-pipeline（另一 skill）；CI（GitHub Actions）永不写
  AxonHub，也不持有其凭据。
- 写入与变换使用**独立的确认材料**，确认其一不授权另一。
- 通道规则：**读可以用 curl**（直接 POST GraphQL，输出是干净 JSON，便于 jq
  解析）；**写必须走 graphql-cli**（token 经 `endpoint login` 注入，凭据不
  散落在命令历史）。

## 任务导航

按任务类型读对应流程文档；所有写任务共用本文后续的 Token、执行循环、对账
读、Payload 约定与部署怪癖。

| 任务 | 何时 | 流程 |
| --- | --- | --- |
| 重算规划（replan） | 快照更新后重算 `models.csv` 与 model-plan；评审映射建议，确认后移交模型卡更新 / Claude 模型关联 | 本文「重算规划」节 |
| 同步渠道清单 | 物化 blocklist 派生类并生成屏蔽正则（`autoSyncModelPattern`）与回读校验；手工静态渠道的 `supportedModels` 维护 | [channel-sync.md](references/channel-sync.md) |
| 模型卡更新 | 把 model-plan 增量应用：建实体、改卡/成本/备注、启用、清理 | [model-card-update.md](references/model-card-update.md) |
| Claude 模型关联 | `models.csv` 确认后写请求模型的关联路由 | [claude-模型关联.md](references/claude-模型关联.md) |
| 非 Claude 模型关联 | `channelPriority` 链、free 变种合并、回退与时段门控 | [非Claude模型关联.md](references/非Claude模型关联.md) |
| 批量重建（rebuild） | 目录清空/迁移后的整体重建 | 本文「批量重建」节 |

## 重算规划（replan）

把 watch-pipeline 快照离线重算成评审产物：`models.csv`（映射建议）与
`data/model-plan.json`（模型增量计划）。纯离线：不联网、不持凭据、不写
AxonHub，任何时刻可以本地重跑。blocklist 作为既有输入只读消费（派生类的
物化归同步渠道清单任务的物化步骤）；怀疑派生排除陈旧（分数刚变化、新速度
变种）时，先跑一次该物化再 replan。

### 输入（均为仓库内快照）

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

### 流程

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
   写任务（模型卡更新 / Claude 模型关联）；确认映射不等于授权写渠道或模型，
   确认材料相互独立。

### 注册表构造规则（评审与排障时对照；数字阈值是脚本内常量——文档解释，代码裁决）

对象侧规则的归属：映射公式与 Arena 匹配链属 Claude 模型关联任务，跨渠道
去重/变种分组/别名归一属非 Claude 模型关联任务，卡片成本与备注属模型卡
更新任务，排除物化属同步渠道清单任务——本节不复述。

- **free 判定**——记录级 `free` 旗标权威（含 `free: false` 反覆盖），无旗标
  时 `-free` 后缀为派生默认（采集刷新的删除重插会丢记录级手工字段，默认值
  保证这类模型不被误判；缺口以 `free_flag_missing` 呈现，供维护者补旗标）。
- **Arena + rp5h 分诊**——非 free 无任何 Arena 匹配则不带分入册但不进映射
  池（`arena_missing`）；缺 `rp5h` 的非 free 模型排除
  （`rp5h_missing_excluded`，有分场景已被 lowscore 物化先行接管，本分支
  实际覆盖查无分数者），分数达到 1500 保留带评审警告
  （`rp5h_missing_review`），仅不参与映射目标挑选（记入 INELIGIBLE）。

### 产物语义

- `models.csv`——request 行保留为输入清单，其余单元格每轮重算；是映射建议
  的可审查快照，不是 AxonHub 运行时状态。
- `model-plan.json`（schema 1）——增量条目：`modelID`、归属渠道、可选
  `channelAliases`、`channelPriority` 链（按 `rp5h` 降序的实际服务渠道）、
  `cardRef`、以及推导 meta + 渠道声明终值组成的 `input`。卡片本身绝不复制
  进计划。渠道清单治理独立于规划产物（同步正则）。
- plan 是**纯目标态**：无指纹与陈旧性机制，过期整体重算；远端漂移由写任务
  的 read-before-write 在执行时发现并报告。

### 移交

- **模型卡** → 模型卡更新任务，用户确认后写入。渠道清单治理独立于本流程
  （同步正则，不消费规划产物）。
- **Claude 映射** → 用户读 `models.csv` 后选择：Claude 模型关联任务写入，
  或 AxonHub UI 手改。日常改靶直接在 UI，不进本流程。
- **Claude 全局模型** → AxonHub 内一次手工创建，永不脚本化。

## Token

1. 环境变量 `AXONHUB_JWT` 非空则直接用（签发后 7 天有效）。
2. 否则用 browser-use 打开 `https://axon.jasonqin.site/`（应已登录），在页面上下文执行
   `localStorage.getItem('axonhub_access_token')` 取 token。
3. 注入 graphql-cli：`npx -y @axonhub/graphql-cli endpoint login axonhub --type token --token "$TOKEN"`。
4. 校验：`curl -X POST .../admin/graphql` 查 `{ me { id email } }`，HTTP 200 且有 `data.me` 即通过；401 则回第 2 步重取。

JWT 是凭据：只出现在 Authorization 头和环境变量里，不打印、不复制、不写入
文件、提交、plan/CSV 或日志。`AXONHUB_JWT`（本 skill，浏览器会话 token）与
`AXONHUB_TOKEN`（axonhub-cli，signin 接口换得）机制不同、互不通用。

## 执行循环（所有批量写操作的标准流程）

```text
loop:
  1  state  = 对账读()               # 只认线上状态
  2  plan   = 生成工作清单(state)     # 顺序规则见各任务文档
  3  确认(plan)                      # 展示工作清单，请求一次确认
  4  for item in plan:
       out = graphql-cli mutate MUT -e axonhub -v '{"id":…,"input":{…}}'
       if out 匹配 "Error:" 或 '"errors"': 记失败   # 两种形态都要查
  5  state2 = 对账读()               # 回读
  assert state2 == 预期终态           # 回读是唯一权威信号
```

纪律条目（每条都有实战出处）：

1. **先对账后执行**。写入依据只能是刚读取的线上状态；被中断/取消的运行是常态，按"剩余工作"
   重新计算，绝不盲目重放。（实例：一次被取消的运行其实已落库 23 个创建和 4 个删除，
   重放会造出重复模型。）
2. **工作清单顺序规则**：删除条件按"成本已更新"的状态评估（先成本后删除判定）；满足删除
   条件的 ID 直接从创建清单剔除（不要建了又删）；已在删除清单里的对象跳过成本/备注更新。
3. **mutation 输入必须用 `-v` 传变量**，且按 mutation 形状嵌套为 `{"id":…, "input":{…}}`。
   把 `modelCard`/`remark` 放在变量顶层会报 `must be defined`（HTTP 422），服务端什么都没写。
4. **错误检测双模式**：CLI 把服务端 4xx 打成 `Error: HTTP 422: {"errors":[…]}` 行（不是干净
   的 JSON 响应），同时响应 JSON 里也可能有 `"errors"`。只 grep 其一会把失败当成功。
   （实例：37 条 updateModel 全部报 OK，实际全被 422 拒绝，靠回读才暴露。）
5. **回读验证是唯一权威**。CLI 的成功输出只是线索：npm notice 噪音、多段 JSON 拼接、
   解析失败都常见——服务端成功而本地解析报失败、反之亦然，都发生过。每项写后回读该对象，
   结束后全量对账计数。
6. **整体替换语义**：`UpdateModelInput` 的 `modelCard`、`UpdateChannelInput.settings`、
   `settings.associations` 都是全量替换——必须"读全对象 → 只改目标字段 → 整体回写"。
   只传 `cost` 会把卡片其余字段清成零值（能力位全变 ×）。
7. **删除语义**：`deleteModel(id)` 是硬删（resolver 跳过软删拦截器，可重建同名）；
   `bulkDeleteModels(ids)` 是软删（返回 true 但不清理，且阻塞同名重建）。以"随后的
   createModel 成功"验证清除，不信返回值。
8. **建后启用**：`createModel` 与 Web UI 建出的模型都是 disabled，用
   `updateModelStatus(id, enabled)` 或 `bulkEnableModels(ids)` 翻转。
9. **修改没有 bulk mutation**：批量改成本/备注/卡片就是逐条 `updateModel` 循环（npx 每次约
   2s 启动开销，可接受）。创建用 `bulkCreateModels(inputs: […])` 一次完成。
10. **删除前查关联引用**——被任何其他模型的 association 规则引用的模型保留并报告，不删。

单渠道/单字段的小治理更新（如 [channel-sync.md](references/channel-sync.md) 的正则
写入）可短路本循环：确认目标值 → 一次 mutate → 回读该对象。任务文档标注了短路权
的以任务文档为准。

## 对账读的标准查询

- 渠道拆成两条查再按 id 拼接（`tags` 与 `settings` 组合查询会报错）：
  `queryChannels(input:{first:50}) { edges { node { id name type status supportedModels manualModels autoSyncSupportedModels … } } }`
  与 `queryChannels(input:{first:50}) { edges { node { id settings { modelMappings { from to } } } } }`
- 模型：`models(first:100) { edges { node { id modelID name developer type status remark modelCard { … } settings { associations { … } } } } }`
  —— 卡片字段取全（reasoning/toolCall/temperature/modalities/vision/cost/limit/knowledge/releaseDate/lastUpdated），
  为整体回写做准备。
- 模板类 relay 连接必须显式 `first:`，否则 `either first or last must be provided`。
- 多个根字段合并在一个请求里会失败（如 models + queryChannels 同发返回 null），拆开发。

## Payload 约定（CreateModelInput 字段映射，卡片数据优先取内置目录）

模型卡更新与批量重建共用的每模型 payload 约定：

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
  `loadBalancerStrategy`/`traceStickyMode`）未变就省略（见部署怪癖）。
- 成本终值已在计划里算好（渠道声明逐字段覆盖、free 归零），payload 直接用
  计划 `input.cost`；重建场景无计划时按同一映射从卡片/目录条目现场组装。

## 批量重建（rebuild）

目录被清空或迁移后的整体重建。单条/增量写入属模型卡更新任务。
调用纪律见上方执行循环，payload 字段映射见上方 Payload 约定。工具：
graphql-cli；大结果读取可用带 JWT 的 curl。

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
   卡片；等价的 GraphQL 流程是查 `providersCatalog(filtered: true)`，按
   上方 Payload 约定从条目构造 `CreateModelInput`（实测 `gpt-5.5` 一次
   成型，卡片完整）。目录没有的 ID 再查 `data/all_models.json`，都没有才
   人工兜底。
5. 创建：`bulkCreateModels(inputs: […])` 一次建齐；每模型 payload 的
   `settings` 为 `{associations: []}`。
6. 建后启用：`bulkEnableModels(ids)` 翻转（建出的都是 disabled）。
7. 关联接线是建完后的独立一轮：非 Claude 自映射与请求模型路由分别属
   非 Claude 模型关联 / Claude 模型关联任务。
8. 每轮之后与线上对账（`models(first:100)`），只补真正缺失的部分。

## 数据源

模型卡片的来源优先级：**① AxonHub 内置目录**（`providersCatalog`，默认上游
`ThinkInAIXYZ/PublicProviderConf` 每小时刷新，拉取失败回退二进制内嵌快照；models 页
「批量添加」从目录条目自动组装完整卡片）→ **② `data/all_models.json`**（models.dev 快照，
离线规划管线用）→ **③ 人工兜底**（渠道特有/最新 ID，两个源都常缺）。

| 文件 | 内容 | 权威范围 |
| --- | --- | --- |
| AxonHub 内置目录 | 21 开发者 453 模型（实测 2026-09-10），含能力位/成本/上限/日期 | 主流模型卡片首选来源 |
| `data/models_extra.json` | 渠道节（每渠道的模型、cost、free 标记）、顶层 `aliases`、顶层 `blocklist`（唯一排除源：`speed:`/`lowscore:` 由 channel-sync 物化，`manual`/`tier`/`retired` 人工维护） | 渠道清单与渠道侧成本；blocklist 同时供渠道同步正则枚举 |
| `data/all_models.json` | models.dev 快照卡片 | 卡片补充来源（覆盖不全，缺卡走人工兜底，不臆造） |
| `data/arena.json` | leaderboard 分数（`arena_score`/`organization`/`effort`） | 备注 `manual` 字段的 `arena_score: <分>` 标签 |

## 部署怪癖与已知事实

- Server：`https://axon.jasonqin.site`（vol-server 上 nginx 反代到 `127.0.0.1:8868`）。
  AxonHub 完整 admin schema 在 axonhub 源码仓库 `internal/server/gql/*.graphql`；本部署可能与快照有漂移，
  形状可疑时优先用 axonhub-cli 的 `find <type> -e axonhub --input --detail` 探线上 schema。
- `when` 条件的根必须是 group：裸 `condition` 会被拒（`root when condition must be a group`）；
- 瞬态 `unknown field` 错误（`GRAPHQL_VALIDATION_FAILED`，部署窗口期出现、自行消失）：
  退避重试 + 回读，不改输入形状；持续报错才是形状问题。
- 可选 settings 字段（`disableDeveloperSettingsInheritance`/`loadBalancerStrategy`/`traceStickyMode`）
  未变就省略——多传反而可能触发瞬态校验器。
- GraphQL ID 是 GID（`gid://axonhub/Model/23`）；association 输入的 `channelId` 用整数。
- 模型 ID 陷阱：上游用厂商前缀 ID（`deepseek/deepseek-v4-flash`、`zai-org/GLM-5.3`），Model 实体用
  裸 ID（`deepseek-v4-flash`），association 按**精确字符串**匹配。两种修复：渠道侧
  `settings.modelMappings`（from=裸 ID，to=前缀 ID）；模型侧 association 链
  （`channel_model` 钉渠道+精确 ID / `model` 全局精确 ID / `regex` 全局正则）。

## 事件响应

怀疑凭据泄露时：立即撤销并轮换 `AXONHUB_JWT`、GitHub Secrets 及服务器密钥 →
检查 Actions 日志、AxonHub 审计与服务日志、最近快照提交 diff → 核对模型卡、
remark、channel supported-models 与 associations 变更 → 确认完整性前保持
workflows disabled，dry-run 验证后再恢复。

## 文档维护

治理逻辑与任务流程只维护在本目录 markdown；流程多次跑稳后再固化为脚本。
`scripts/` 与 `tests/` 现含离线规划器（`models_mapping.py`）、写时组装
（`assemble_card.py`）及其测试，校验：`python3 -m pytest -q`。
