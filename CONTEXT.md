# Model Routing

本项目维护 OpenCode 模型目录，并把固定的 Claude/GPT 下游请求模型映射到可用的 OpenCode 模型。模型目录同步与下游映射是两个相邻但独立的领域：前者维护可用模型事实，后者维护兼容请求的选择。

本文件是术语唯一真源。其他文件引用术语时以此处定义为准，不复述定义。

## 模型目录

**Provider**：发布一组模型标识符及其协议能力的上游服务命名空间。
_Avoid_: Channel, vendor

**OpenCode model**：由 OpenCode provider 提供、可以作为 AxonHub 上游调用目标的模型。
_Avoid_: Candidate（当模型只是在描述目录成员时）

**Protocol channel**：承载同一协议模型调用的 AxonHub 通道。一个模型可以因协议不同而属于不同通道。
_Avoid_: Model association, route

**Managed channel**：`supportedModels` 必须由本项目维护为显式 allowlist 的 AxonHub channel。判定标准是上游模型清单（如 `/v1/models` 返回值）超出账号实际可用集合、需要人工策展，而非是否恰好有写权限。当前托管哪些渠道是易变事实，由配置记录，术语表不枚举渠道。
_Avoid_: Any enabled channel, provider channel

**Entitlement**：账号在某渠道实际有权使用的模型集合。上游广告的模型清单可能超出 entitlement；托管渠道的目录必须是 entitlement 的显式 allowlist，其事实来源因渠道而异。
_Avoid_: 全量模型清单, 套餐模型列表

**Model card**：描述模型能力、限制、模态、价格和版本信息的公共模型资料。
_Avoid_: Model config, remark

**Model remark**：附在模型上的结构化补充资料，包含 `rp5h`、`usage_quota`、`context_threshold`、`peak_hours`、`retention` 和人工备注。
_Avoid_: Free-form note, metadata

**Catalog exclusion**：因不在 cache/go 交集或经人工决定而不属于 managed catalog 的模型。排除不等同于删除全局 model object。
_Avoid_: Tombstone, stale model

**Model decision**：对交集内缺失数据模型作出的、带理由的人工 exclude 或 supplement 事实。
_Avoid_: CSV edit, inferred fallback

## 下游映射

**Request model**：下游客户端请求的固定 Claude 或 GPT 模型标识符。它必须预先存在于 AxonHub，映射流程不负责创建或删除它。
_Avoid_: User model, external model, source model

**Candidate model**：可以承接 request model 请求的 OpenCode model。
_Avoid_: Target assignment（将关系与模型混为一谈）

**Mapping**：一个 request model 与一个 candidate model 之间的一对一兼容关系。
_Avoid_: Fallback chain, channel routing

**Managed template**：由本项目维护 canonical mapping 的 `stable`、`claude`、`gpt` API-key profile template。维护范围外的 mappings 作为人工事实保留。
_Avoid_: Any profile template, dated template

**Mapping workspace**：由 Arena 数据、OpenCode 补充数据和固定 request model 集合确定性生成的审核表；它是映射建议的可审查快照，不是 AxonHub 的运行时状态。
_Avoid_: Source of truth, handoff CSV

**Baseline request model**：同一 Claude 或 GPT series 中 Arena 分数最低的 request model，使用专门的基础模型选择规则。
_Avoid_: Cheap model, entry-level model

**Series**：共享 `claude-` 或 `gpt-` 前缀的一组 request models。
_Avoid_: Provider group, family

**Arena score**：Arena 榜单对模型表现给出的质量信号，用于比较 request model 与 candidate model。
_Avoid_: Price score, quota score

**Match confidence**：Arena 记录与模型标识符关联时的证据强度，例如 direct、contributor suffix、version downgrade 或 prefix match。
_Avoid_: Mapping certainty（除非明确指最终映射）

## 变更门禁

**Warning**：不妨碍模型目录同步、但必须在报告中披露的资料缺口或兼容性提示。
_Avoid_: Ignored error

**Decision required**：影响 mapping 的资料缺失，必须由 model decision 解决后才能 apply。
_Avoid_: Warning, automatic fill

**Blocking error**：使本次映射或 AxonHub 写入不安全的错误；出现时只能生成报告，不能 apply。
_Avoid_: Fatal warning
