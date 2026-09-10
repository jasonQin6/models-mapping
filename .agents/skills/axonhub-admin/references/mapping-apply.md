# 应用 Claude 映射（mapping-apply）

把 `models.csv` 评审确认的映射写入 AxonHub：每个请求模型一条指向确认候选的
关联路由。前置：映射已经 [replan.md](replan.md) 呈报且用户对映射表明确确认；
确认映射不等于授权写渠道清单或模型卡（确认材料相互独立）。

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
