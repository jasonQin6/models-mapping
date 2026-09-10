# 批量重建（rebuild）

目录被清空或迁移后的整体重建。单条/增量写入走 [model-sync.md](model-sync.md)。
调用纪律（mutation 变量、双模式错误检测、CLI 噪音、先对账后执行、整体替换）
全部见 [SKILL.md](../SKILL.md) 的执行循环；payload 字段映射见
[model-sync.md](model-sync.md) 的 Payload 约定。工具：graphql-cli；大结果
读取可用带 JWT 的 curl。

## 流程

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
   Payload 约定从条目构造 `CreateModelInput`（实测 `gpt-5.5` 一次成型，卡片
   完整）。目录没有的 ID 再查 `data/all_models.json`，都没有才人工兜底。
5. 创建：`bulkCreateModels(inputs: […])` 一次建齐；每模型 payload 的
   `settings` 为 `{associations: []}`。
6. 建后启用：`bulkEnableModels(ids)` 翻转（建出的都是 disabled）。
7. 关联接线是建完后的独立一轮：非 Claude 自映射走 [routing.md](routing.md)，
   请求模型路由走 [mapping-apply.md](mapping-apply.md)。
8. 每轮之后与线上对账（`models(first:100)`），只补真正缺失的部分。
