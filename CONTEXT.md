# Model Routing

模型目录同步与下游映射是两个相邻但独立的领域：前者维护可用模型事实，后者维护兼容请求的选择。

本文件是术语唯一真源。其他文件引用术语时以此处定义为准，不复述定义。
AxonHub 相关概念以页面与接口自带的叫法为准（页面用词：模型、渠道、服务商、
支持的模型、关联模型、关联规则、关联渠道、负载均衡策略、会话粘性）。

## 模型目录

**Model（模型）**：AxonHub 里的全局模型实体，页面叫"模型"，按 `modelID` 精确
标识，字段含名称、开发者、类型、状态与模型卡。
_Avoid_: 模型卡实体（模型卡只指 modelCard 资料）、目标实体

**Channel（渠道）**：AxonHub 页面"渠道"下的服务通道，按名称（如
`commandcode-goat`）标识，字段含服务商、状态、权重与支持的模型。
_Avoid_: Provider（Provider 特指发布模型标识符的上游命名空间）

**Provider**：发布一组模型标识符及其协议能力的上游服务命名空间；渠道页叫
"服务商"。
_Avoid_: Channel, vendor

**Protocol channel**：承载同一协议模型调用的 AxonHub 通道。一个模型可以因协议不同而属于不同通道。
_Avoid_: Model association, route

**Managed channel**：`supportedModels` 必须由本项目维护为受治理清单的 AxonHub channel。判定标准是上游模型清单（如 `/v1/models` 返回值）超出账号实际可用集合、需要人工策展，而非是否恰好有写权限。当前托管哪些渠道是易变事实，由 `models_extra.json` 的渠道节记录（AxonHub 渠道名 = 节名），术语表不枚举渠道。维护方式：autoSync 渠道开启 AxonHub 自带同步并用 `autoSyncModelPattern` 枚举屏蔽（规则与差集数据见 axonhub-admin 的 channel-sync 流程）；embedding/作图类低频渠道为手工静态清单（同步保持关闭）。
_Avoid_: Any enabled channel, provider channel

**Supported models（支持的模型）**：渠道页"支持的模型"列——autoSync 按存储正则过滤后的渠道模型清单。被屏蔽的 id 不会出现，因此它反推不了上游原始清单。
_Avoid_: 原始清单, 全量模型清单

**Entitlement**：账号在某渠道实际有权使用的模型集合。上游广告的模型清单可能超出 entitlement；托管渠道的目录必须是 entitlement 的显式 allowlist，其事实来源因渠道而异。
_Avoid_: 套餐模型列表

**Allowlist**：托管渠道 entitlement 的显式模型清单，即 `models_extra.json` 对应渠道节在去重、blocklist 排除与人工 exclude 之后的键集合。它是事实源的直接产物、由生产端硬门禁（结构漂移整轮失败）保护；autoSync 渠道在 AxonHub 侧以同一份屏蔽条目的枚举正则表达同一策展（同步清单 − 屏蔽 ≈ 授权清单）。
_Avoid_: Model filter, intersection result

**Blocklist（屏蔽清单）**：`data/blocklist.json` 的渠道屏蔽条目（channel → `[{id, reason}]`），autoSyncModelPattern 的唯一数据源。`speed:`/`lowscore:`/`superseded:` 三类由脚本从快照重建，`manual`/`tier`/`retired` 人工维护。
_Avoid_: 黑名单, 排除状态散落在模型记录上

**Model card（模型卡）**：描述模型能力、限制、模态、价格和版本信息的公共模型资料（AxonHub 字段 `modelCard`）。
_Avoid_: Model config, remark

**Model remark**：附在模型上的结构化补充资料，包含 `rp5h`、`usage_quota` 和人工备注。
_Avoid_: Free-form note, metadata


**Model variant**：同一基础模型的衍生 id（如 `-free`、`-contributor`、`-fast` 后缀）。尺寸后缀（如 `-27b`）是模型 id 的一部分，不构成变种关系。
_Avoid_: Alias（别名指跨渠道对同一 id 的拼写归一）

**目标清单**：axonhub-admin ② 从快照算出的模型卡应有状态清单（`modelID`、归属渠道、`cardRef`、成本、备注）。写入前与线上实体做差集。
_Avoid_: 目标态、注册表（这两个自创词已弃用）


## 下游映射

**Association（关联模型）**：模型页"模型关联"里配置的关联规则（模型
`settings.associations`），把请求路由到渠道内精确 ID；类型 `channel_model`
钉住渠道+ID。页面叫"关联规则/关联渠道/渠道内精准匹配模型"。
_Avoid_: 路由链, Fallback chain

**Request model**：下游客户端请求的固定 Claude 模型标识符。它必须预先存在于 AxonHub，映射流程不负责创建或删除它。
_Avoid_: User model, external model, source model

**Candidate model**：可以承接 request model 请求的上游 model。
_Avoid_: Target assignment（将关系与模型混为一谈）

**Mapping**：一个 request model 与一个 candidate model 之间的一对一兼容关系。
_Avoid_: Fallback chain, channel routing

**Requests 清单**：`claude_map.py` 的 `REQUESTS` 字典（claude-\* id → 人工备注），人工维护的请求模型事实源，加删条目即增减请求模型。映射建议由 Claude 映射步骤的脚本确定性计算、stdout 呈报，不落中间产物文件，也不是 AxonHub 的运行时状态。
_Avoid_: Mapping workspace, handoff CSV

**Free fill**：free 模型作为 request model 专属供给的分配机制；free 池耗尽后剩余 request 由公式在非 free 候选上承接。
_Avoid_: Baseline routing, free priority

**Arena score**：Arena 榜单或人工赋分给出的模型质量信号，用于比较 request model 与 candidate model。
_Avoid_: Price score, quota score


## 变更门禁

**Warning**：不妨碍模型目录同步、但必须在报告中披露的资料缺口或兼容性提示。
_Avoid_: Ignored error

**Decision required**：影响 mapping 的资料缺失，必须由 model decision 解决后才能 apply。
_Avoid_: Warning, automatic fill

**Blocking error**：使本次映射或 AxonHub 写入不安全的错误；出现时只能生成报告，不能 apply。
_Avoid_: Fatal warning
