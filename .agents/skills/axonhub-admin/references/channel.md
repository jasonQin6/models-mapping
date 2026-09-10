# 渠道清单（channel allowlists）

本仓库规划的第一类 AxonHub 对象：每个渠道的 `supportedModels`——精确的裸 ID 允许清单。
AxonHub 渠道名等于其在 `data/models_extra.json` 里的节名，生成的 `data/channel-plan.json`
（schema 1）为每个节携带一份精确、排序的原生 ID 清单。该产物取代 AxonHub 的
`autoSyncSupportedModels`：每个写人工清单的渠道上游自动同步必须保持关闭，计划是
"该渠道可以服务哪些模型"的权威人工来源。哪些渠道被治理由节本身记录；`ant` 与
`sensenova` 是人工维护的静态节，任何 watcher 不得写入。

候选全集从渠道节的每条记录出发（渠道提供的 `claude-*` 模型永不采集——Claude 在
AxonHub 内自建），再经以下规则收敛：

- **别名归一**——`data/models_extra.json` 人工维护的顶层 `aliases` 映射（如
  `tencent-hy3` → `hy3`）合并跨渠道拼写。渠道清单保留原生 ID；注册表与 `plan.models[]`
  用规范 ID，`channelAliases` 描述各渠道的路由暴露。
- **速度营销变种**——以 `-fast`/`-highspeed` 结尾的 ID 在规划期判定为排除。
- **模型决策**——记录上人工维护的 `exclude` 原因把模型挡在清单外；字段级合并保证该
  原因跨采集轮次保留。
- **跨渠道去重**——每个 ID 一个胜出渠道：`rp5h` 最高者胜（null 输给有值，平局取字母序
  靠前的渠道）。每次裁决都报告 `duplicate_model_across_sources`。
- **变种分组**——同一基础模型内，`-free` 压过 `-contributor` 压过原始版；被取代的
  变种以 `variant_superseded` 离场。
- **rp5h 分诊**——缺 `rp5h` 的非 free 模型，Arena 分低于 1500 时排除
  （`rp5h_missing_excluded`）；达到 1500 则留在允许清单并带评审警告
  （`rp5h_missing_review`），只是不参与映射目标挑选。

free 是声明的渠道事实——记录上人工维护的 `free: true` 字段，与 ID 拼写无关。对所属
渠道，free 模型的 `rp5h` 从该渠道最大的非 free `rp5h` 重新推导（渠道没有非 free 基准
时回退 1000），缺失的 `usage_quota` 补 60（`free_default_filled`）。
