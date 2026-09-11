# Model Routing

模型目录同步与下游映射是两个相邻但独立的领域：前者维护可用模型事实，后者维护兼容请求的选择。

本文件是术语唯一真源。其他文件引用术语时以此处定义为准，不复述定义。

## 模型目录

**Provider**：发布一组模型标识符及其协议能力的上游服务命名空间。
_Avoid_: Channel, vendor

**Protocol channel**：承载同一协议模型调用的 AxonHub 通道。一个模型可以因协议不同而属于不同通道。
_Avoid_: Model association, route

**Managed channel**：`supportedModels` 必须由本项目维护为受治理清单的 AxonHub channel。判定标准是上游模型清单（如 `/v1/models` 返回值）超出账号实际可用集合、需要人工策展，而非是否恰好有写权限。当前托管哪些渠道是易变事实，由 `models_extra.json` 的渠道节记录（AxonHub 渠道名 = 节名），术语表不枚举渠道。维护方式：autoSync 渠道开启 AxonHub 自带同步并用 `autoSyncModelPattern` 允许正则屏蔽（规则与差集数据见 axonhub-admin 的 channel-sync 流程）；embedding/作图类低频渠道为手工静态清单（同步保持关闭）。
_Avoid_: Any enabled channel, provider channel

**Entitlement**：账号在某渠道实际有权使用的模型集合。上游广告的模型清单可能超出 entitlement；托管渠道的目录必须是 entitlement 的显式 allowlist，其事实来源因渠道而异。
_Avoid_: 全量模型清单, 套餐模型列表

**Allowlist**：托管渠道 entitlement 的显式模型清单，即 `models_extra.json` 对应渠道节在去重、blocklist 排除与人工 exclude 之后的键集合。它是事实源的直接产物、由生产端硬门禁（结构漂移整轮失败）保护；autoSync 渠道在 AxonHub 侧以允许正则表达同一策展（同步清单 − 屏蔽 ≈ 授权清单）。
_Avoid_: Model filter, intersection result

**Model card**：描述模型能力、限制、模态、价格和版本信息的公共模型资料。
_Avoid_: Model config, remark

**Model remark**：附在模型上的结构化补充资料，包含 `rp5h`、`usage_quota` 和人工备注。
_Avoid_: Free-form note, metadata



**Model variant**：同一基础模型的衍生 id（如 `-free`、`-contributor`、`-fast` 后缀）。尺寸后缀（如 `-27b`）是模型 id 的一部分，不构成变种关系。
_Avoid_: Alias（别名指跨渠道对同一 id 的拼写归一）


## 下游映射

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
