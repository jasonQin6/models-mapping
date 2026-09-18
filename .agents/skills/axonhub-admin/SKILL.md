---
name: axonhub-admin
description: 本仓对 AxonHub 部署（https://axon.jasonqin.site）的四步治理流水线：渠道同步过滤正则（blocklist 物化 + autoSyncModelPattern）、模型卡增量更新、非 Claude 关联路由（channelPriority 回退链）、Claude 映射（Arena 加权公式 + free 补全）。每步一个离线计算脚本（stdout 呈报，无中间产物文件）加确认后的受控写入。凡任务涉及刷新渠道清单、更新模型卡/成本/备注、写关联路由、或把上游模型映射到 Claude 请求模型，都先用本 skill。工具机制（token、endpoint、find/query/mutate 用法）以 axonhub-cli skill 为准；仓库外的一次性通用查询也用 axonhub-cli。
---

# AxonHub Admin

对 AxonHub 的管理操作全部走 `/admin/graphql`，鉴权用 JWT token（不是静态 key）。
本 skill 是 axonhub-cli 的 wrapper：**怎么连、怎么调**见 axonhub-cli；**做什么、
按什么纪律做**见本文与 `references/` 的任务流程。

## 定位与分层

- 两阶段：**离线计算**（每步一个脚本，只消费仓库内快照，不联网、不持凭据、
  可随时重跑，stdout 呈报）→ **受控写入**（交互会话、凭据、逐次确认）。
  没有中间产物文件：差集（目标态 × 线上状态）在会话里对照得出。
- 采集只发生在 watch-pipeline（另一 skill）；CI（GitHub Actions）永不写
  AxonHub，也不持有其凭据。
- 写入与变换使用**独立的确认材料**，确认其一不授权另一。
- 通道规则：**读可以用 curl**（直接 POST GraphQL，输出是干净 JSON，便于 jq
  解析）；**写必须走 graphql-cli**（token 经 `endpoint login` 注入，凭据不
  散落在命令历史）。

## 流水线（四步串联）

采集（watch-pipeline）→ ① 渠道过滤 → ② 模型卡 → ③ 非 Claude 关联 →
④ Claude 关联。②③④ 共享同一注册表（`scripts/snapshot.py` 加载快照 +
`scripts/registry.py` 去重/变种/free/分诊），①拥有 blocklist 物化——
注册表只消费物化后的 blocklist。各步可独立重跑；快照更新后按需从①或②起跑。

| 步骤 | 任务 | 计算/工具（仓库根运行） | 流程 |
| --- | --- | --- | --- |
| ① | 同步渠道清单：物化派生 blocklist、生成屏蔽正则 | `channel_sync.py` | [channel-sync.md](references/channel-sync.md) |
| ② | 模型卡更新：目标态增量、建/改/启/清理、整体重建 | `model_card_update.py [--id <modelID>]` | [model-card-update.md](references/model-card-update.md) |
| ③ | 非 Claude 模型关联：channelPriority 回退链 | `channel_assoc.py [--id <modelID>]` | [非Claude模型关联.md](references/非Claude模型关联.md) |
| ④ | Claude 模型关联：请求 → 候选映射（请求清单 = 脚本内 `REQUESTS` 字典） | `claude_map.py` | [claude-mapping.md](references/claude-mapping.md) |

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

纪律条目（每条都有实战出处；模型卡写侧的细节纪律见 model-card-update.md）：

1. **先对账后执行**。写入依据只能是刚读取的线上状态；被中断/取消的运行是常态，按"剩余工作"
   重新计算，绝不盲目重放。（实例：一次被取消的运行其实已落库 23 个创建和 4 个删除，
   重放会造出重复模型。）
2. **mutation 输入必须用 `-v` 传变量**，且按 mutation 形状嵌套为 `{"id":…, "input":{…}}`。
   把 `modelCard`/`remark` 放在变量顶层会报 `must be defined`（HTTP 422），服务端什么都没写。
3. **错误检测双模式**：CLI 把服务端 4xx 打成 `Error: HTTP 422: {"errors":[…]}` 行（不是干净
   的 JSON 响应），同时响应 JSON 里也可能有 `"errors"`。只 grep 其一会把失败当成功。
   （实例：37 条 updateModel 全部报 OK，实际全被 422 拒绝，靠回读才暴露。）
4. **回读验证是唯一权威**。CLI 的成功输出只是线索：npm notice 噪音、多段 JSON 拼接、
   解析失败都常见——服务端成功而本地解析报失败、反之亦然，都发生过。每项写后回读该对象，
   结束后全量对账计数。
5. **整体替换语义**：`UpdateModelInput` 的 `modelCard`、`UpdateChannelInput.settings`、
   `settings.associations` 都是全量替换——必须"读全对象 → 只改目标字段 → 整体回写"。
   只传 `cost` 会把卡片其余字段清成零值（能力位全变 ×）。

单渠道/单字段的小治理更新（如 [channel-sync.md](references/channel-sync.md) 的正则
写入）可短路本循环：确认目标值 → 一次 mutate → 回读该对象。任务文档标注了短路权
的以任务文档为准。

## 数据源

| 文件 | 内容 | 权威范围 |
| --- | --- | --- |
| `data/models_extra.json` | 渠道节（每渠道的模型、cost）、顶层 `aliases`、顶层 `blocklist`（唯一排除源：`speed:`/`lowscore:` 由 channel_sync.py 物化，`manual`/`tier`/`retired` 人工维护） | 渠道清单与渠道侧成本；`aliases`/`blocklist` 人工维护 |
| `data/all_models.json` | models.dev 快照卡片 | ②的卡片来源（覆盖不全，缺卡走内置目录/人工兜底，不臆造） |
| `data/arena.json` | leaderboard 分数（`arena_score`/`organization`/`effort`，`manual: true` 人工指派） | ①物化与④映射的质量信号 |

请求模型的 free 判定横切各步，只读渠道声明价：input/output 均声明为零价即免费；
正价或未声明即非免费，未声明的非免费模型交 rp5h 缺失门禁。零价由采集层统一供给：
watcher 转写渠道价格表的 `Free` 字样（go/goat），ant/sensenova 静态节人工以零价
声明——判定链上不存在旗标或 id 后缀启发式。判定免费而渠道未声明缓存价时，卡片
成本以零起步（`_merge_channel_cost`）。

## 部署怪癖与已知事实

- Server：`https://axon.jasonqin.site`（vol-server 上 nginx 反代到 `127.0.0.1:8868`）。
  AxonHub 完整 admin schema 在 axonhub 源码仓库 `internal/server/gql/*.graphql`；本部署可能与快照有漂移，
  形状可疑时优先用 axonhub-cli 的 `find <type> -e axonhub --input --detail` 探线上 schema。
- 模板类 relay 连接必须显式 `first:`，否则 `either first or last must be provided`；
  多个根字段合并在一个请求里会失败（如 models + queryChannels 同发返回 null），拆开发；
  渠道的 `tags` 与 `settings` 也不能组合在同一条 channel 查询里。
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

治理逻辑与任务流程只维护在本目录 markdown；每个任务一个同名脚本
（`channel_sync.py`/`model_card_update.py`/`channel_assoc.py`/`claude_map.py`，
共享 `snapshot.py`/`registry.py`/`name_matching.py`）。校验：`python3 -m pytest -q`。
