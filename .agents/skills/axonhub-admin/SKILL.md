---
name: axonhub-admin
description: 执行本仓库对 AxonHub 部署（https://axon.jasonqin.site）的受控写操作——模型目录、渠道清单、关联路由、成本与备注的批量读写。作为 axonhub-cli 的 wrapper：工具机制（token、endpoint、find/query/mutate 用法）以 axonhub-cli skill 为准，本文持有治理逻辑、执行纪律与部署约定。仓库外的一次性通用查询用 axonhub-cli。
---

# AxonHub Admin

对 AxonHub 的管理操作全部走 `/admin/graphql`，鉴权用 JWT token（不是静态 key）。
本 skill 是 axonhub-cli 的 wrapper：**怎么连、怎么调**见 axonhub-cli；**做什么、按什么纪律做**见本文。

## 定位与分层

- **axonhub-cli**：通用工具机制——token 获取、endpoint 配置、`find`/`query`/`mutate` 的语法与参数。本文不复制其正文，机制细节以它为准。
- **axonhub-admin（本文）**：三类对象的治理逻辑（渠道清单 / 模型卡 / 关联路由，见 `references/` 文档）、数据源约定、执行循环、部署怪癖。
- 通道规则：**读可以用 curl**（直接 POST GraphQL，输出是干净 JSON，便于 jq 解析）；**写必须走 graphql-cli**（token 经 `endpoint login` 注入，凭据不散落在命令历史）。

## Token

1. 环境变量 `AXONHUB_JWT` 非空则直接用（签发后 7 天有效）。
2. 否则用 browser-use 打开 `https://axon.jasonqin.site/`（应已登录），在页面上下文执行
   `localStorage.getItem('axonhub_access_token')` 取 token。
3. 注入 graphql-cli：`npx -y @axonhub/graphql-cli endpoint login axonhub --type token --token "$TOKEN"`。
4. 校验：`curl -X POST .../admin/graphql` 查 `{ me { id email } }`，HTTP 200 且有 `data.me` 即通过；401 则回第 2 步重取。

JWT 是凭据：只出现在 Authorization 头和环境变量里，不写入文件、提交或日志。

## 执行循环（所有批量写操作的标准流程）

```text
loop:
  1  state  = 对账读()               # 只认线上状态
  2  plan   = 生成工作清单(state)     # 顺序规则见下
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
10. **循环内两处机械检查**：要写 `supportedModels` 人工清单的渠道，其
    `autoSyncSupportedModels` 必须为关（开着会被每小时上游同步覆盖，停手报告）；
    删除前查关联引用——被任何其他模型的 association 规则引用的模型保留并报告，不删。

## 对账读的标准查询

- 渠道拆成两条查再按 id 拼接（`tags` 与 `settings` 组合查询会报错）：
  `queryChannels(input:{first:50}) { edges { node { id name type status supportedModels manualModels autoSyncSupportedModels … } } }`
  与 `queryChannels(input:{first:50}) { edges { node { id settings { modelMappings { from to } } } } }`
- 模型：`models(first:100) { edges { node { id modelID name developer type status remark modelCard { … } settings { associations { … } } } } }`
  —— 卡片字段取全（reasoning/toolCall/temperature/modalities/vision/cost/limit/knowledge/releaseDate/lastUpdated），
  为整体回写做准备。
- 模板类 relay 连接必须显式 `first:`，否则 `either first or last must be provided`。
- 多个根字段合并在一个请求里会失败（如 models + queryChannels 同发返回 null），拆开发。

## 数据源

| 文件 | 内容 | 权威范围 |
| --- | --- | --- |
| `data/models_extra.json` | 渠道节（每渠道的模型、cost、free 标记、exclude）、顶层 `aliases` | 渠道清单与渠道侧成本 |
| `data/all_models.json` | models.dev 快照卡片 | 模型卡事实来源（覆盖不全，缺卡走人工兜底，不臆造） |
| `data/arena.json` | leaderboard 分数（`arena_score`/`organization`/`effort`） | 备注 `manual` 字段的 `arena_score: <分>` 标签 |

## 对象规则文档（references/）

- [channel.md](references/channel.md) — 渠道 `supportedModels` 清单的规划与治理规则（别名、去重、变种、free 语义）
- [models.md](references/models.md) — 模型卡与备注的组装规则（单一卡片来源、写时组装、渠道 cost 逐字段覆盖）
- [associations.md](references/associations.md) — 关联路由（Claude 请求模型按分数映射、其余模型自映射的路由约定）
- [batch-creation.md](references/batch-creation.md) — 批量（重）建模型的 playbook（payload 约定、软删清理）

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

## 文档维护

治理逻辑只维护在本目录这些 markdown 里；执行流程多次跑稳后再考虑固化为脚本。
`model-registry` 是遗留的离线规划 skill（其 scripts/tests 仍被本文引用），待本 skill 验证可用后整体删除。
