# 同步渠道清单（channel-sync）

把 `data/channel-plan.json` 应用到 AxonHub 渠道的 `supportedModels`——精确的
裸 ID 允许清单，取代 AxonHub 的 `autoSyncSupportedModels`：计划是"该渠道可以
服务哪些模型"的权威人工来源。AxonHub 渠道名等于其在 `data/models_extra.json`
里的节名。`supportedModels` 是整体替换语义。

## 流程

1. Token（见 [SKILL.md](../SKILL.md) 的 Token 节）。
2. 对账读：`queryChannels` 拆两条查再按 id 拼接（`tags` 与 `settings` 组合
   查询会报错；形状见 SKILL.md 的标准查询）。
3. 工作清单：逐渠道 diff 计划期望 `supportedModels` vs 线上现值，只写有差的
   渠道；重算"剩余工作"，绝不盲目重放被中断的运行。
4. 机械检查：要写人工清单的渠道 `autoSyncSupportedModels` 必须为关——开着
   会被每小时上游同步覆盖，发现开启即停手报告，不得在开启状态下写入。
5. 确认门：向用户展示工作清单，一次确认。
6. 写入：`updateChannel` 经 graphql-cli mutate（`-v` 按形状嵌套变量）整体
   回写 `supportedModels`。
7. 回读验证：重查该渠道确认清单落库；结束后全量对账计数。

## 规则

- 只碰托管渠道（由 `models_extra.json` 渠道节记录）；`ant` 与 `sensenova`
  是人工维护静态节，不写入。
- 渠道自带的 `claude-*` 模型不进清单：Claude 由 AxonHub 自建全局模型 +
  Arena 映射供给。

## 屏蔽差集刷新（autoSyncModelPattern）

开 autoSync 的渠道在同步入口用正则拦截授权外模型；屏蔽列表维护在
`data/models_extra.json` 顶层 `blocklist` 键（渠道 → ID 列表），文档不罗列。

1. 对账读该渠道的实时同步清单与授权清单（如 commandcode-goat 的 goat 节）。
2. 差集 = 同步清单 − 授权清单。
3. 生成排除正则：条目按 `(^|/)ID($|[:/])` 匹配、容忍 `:free` 后缀
   （regexp2 语法支持负向断言）。
4. `updateChannel` 写回 `autoSyncModelPattern` 并触发重同步，回读确认。

授权清单内的 `-fast`/`-highspeed` 变种（如 commandcode 的
`deepseek-v4-flash-fast`）是正价授权，不做一刀切屏蔽。
