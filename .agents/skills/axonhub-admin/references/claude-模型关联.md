# Claude 模型关联

把 `models.csv` 评审确认的映射写入 AxonHub：每个请求模型一条指向确认候选的
关联路由。前置：映射已经 [replan.md](replan.md) 呈报且用户对映射表明确确认；
确认映射不等于授权写渠道清单或模型卡（确认材料相互独立）。

## free 池默认值（free_default_filled / arena_defaulted）

free 补全的配对依赖两组规划期默认值，自动补齐：

- free 模型的 `rp5h` 从所属渠道最大的非 free `rp5h` 重新推导（渠道无非 free
  基准时回退 1000）；缺失的 `usage_quota` 补 60（`free_default_filled`）。
- 剥离变种后缀后仍无 Arena 匹配的 free 模型默认 1500 分并留在池里
  （`arena_defaulted`），保证 free 补全永远有供给——free 池按分升序与升序
  请求配对，最弱请求拿分最低的 free 模型。

## 流程

1. Token（见 [SKILL.md](../SKILL.md) 的 Token 节）。
2. 对账读：`models(first:100)` 含 `settings { associations { … } }`；确认每个
   请求模型已存在且为全局实体。
3. 工作清单：每个请求模型 → 恰好一条**启用的** `type=model` association 指向
   确认的候选。这是唯一整体替换 `settings.associations` 的场景（由本 skill
   仅为固定请求模型写入）；其他模型的既有 associations 不碰。
4. 确认门：展示"请求 → 候选"清单，一次确认。
5. 写入：逐条 `updateModel`，`settings` 按形状嵌套在 `-v` 变量里整体回写。
6. 验证：`queryModelChannelConnections(associations: $assocs)`——目标渠道
   解析出预期 `actualModel` 且 `source: mapping` 或 `direct` 即完成。

## 规则

- 只有 `claude-*` 请求参与映射；GPT 请求按名字直通，从不进入映射。
- 请求模型必须预先存在于 AxonHub（映射流程不创建/删除它）；Claude 全局
  模型一次手工创建，永不脚本化。
- 没有人工覆盖层：写入的就是评审确认的配对；日常改靶直接在 AxonHub UI
  进行，不进本流程。
