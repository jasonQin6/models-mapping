# 非 Claude 模型关联

非 Claude 全局模型自映射：其 association 把裸 ID 模型绑定到服务它的渠道条目。
前提是模型 ID 陷阱——上游用厂商前缀 ID（`deepseek/deepseek-v4-flash`），Model
实体用裸 ID，association 按精确字符串匹配（详见 [SKILL.md](../SKILL.md) 的
部署怪癖）。

## 注册表裁决（channelPriority 的来源）

`channelPriority` 链由共享注册表的三组裁决决定（`duplicate_model_across_sources`/
`variant_superseded` 警告即后两组的审计记录），脚本输出：

```bash
python3 .agents/skills/axonhub-admin/scripts/channel_assoc.py [--id <modelID>]
```

- **别名归一**——`models_extra.json` 顶层人工维护的 `aliases` 映射（如
  `tencent-hy3` → `hy3`）合并跨渠道拼写：去重与分组都以规范 ID 进行；注册表
  用规范 ID，各渠道实际暴露的原生拼写记入 `channelAliases`，供 association
  按渠道精确路由。
- **跨渠道去重**——一个 id 被多个渠道声明时归属 `rp5h` 最高者（null 输给有值，
  平局取字母序靠前渠道）。胜出渠道即 p0 主用，其余实际服务渠道按 `rp5h` 降序
  排为回退——这就是回退链的来源。
- **变种分组**——同一基础模型内 `-free` 压过 `-contributor` 压过原始版；被取代
  的变种以 `variant_superseded` 离场（如 `longcat-2.0` → `longcat-2.0-free`、
  `muse-spark-1.2` → `-contributor`）。基版离场后由胜出变种承接实体，配合下文
  的 free 变种合并落到同一条 association 链上。

脚本输出的 `channelPriority` 即默认链形状：`channel_model` 规则按 rp5h 降序、
优先级数字即回退顺序；每条要钉住的原生 ID 取 `channelAliases` 中该渠道的
拼写，未列出的渠道用规范 ID——association 输入的整数 `channelId` 属线上
状态，对账读后替换。

## 流程

1. Token（见 [SKILL.md](../SKILL.md) 的 Token 节）。
2. 跑脚本拿 `channelPriority`（见上）。
3. 默认形态：按该顺序的 `channel_model` 链——p0 主用（去重胜出渠道），其后是
   回退顺序；单渠道模型退化为 p0 钉死。
4. free 变种合并：一个 free 家族合并到同一全局模型下——主规则指向基础变种，
   兄弟 free 变种作为渠道内回退（`ling-3.0-flash` → ant 的 `vl`/`sante`/
   `fin` 于 p0/p1/p2）。
5. 可选强化（按需逐模型采纳，不是默认）：
   - **严格回退降级**——主规则保持 p0 并带 `exclude: [{channelIds: [<回退渠道>]}]`，
     回退渠道获得 p1 `channel_model` 规则：日常流量永不碰它，429 和故障才会。
   - **时段门控路由**——free 渠道上的 p0 规则包在 `when` 的 daily_time group
     里，不设限的主池降为 p1（输入形状见 SKILL.md 的部署怪癖）。
6. 写入走 SKILL.md 的执行循环：`settings.associations` 是全量替换——读全对象、
   只改目标字段、整体回写；`-v` 按形状嵌套变量。
7. 验证：`queryModelChannelConnections(associations: $assocs)`——目标渠道解析
   出预期 `actualModel` 且 `source: mapping` 或 `direct` 即完成。

## 约定

- association 类型：`channel_model`（钉住渠道+精确 ID）、`model`（全部渠道中的
  精确 ID）、`regex`（全局模式）。曾经的全局 `regex` 默认（到处
  `(?i)(^|/)deepseek-v4-flash$`）已退役；把模型锁定到特定渠道依旧是例外用法。
- 优先级数字升序即回退顺序：p0 主用，p1/p2 回退；同一渠道上的多条
  `channel_model` 规则带升序优先级构成渠道内回退链。
- `when` 条件的根必须是 group：裸 `condition` 会被拒
  （`root when condition must be a group`）。
- association 输入的 `channelId` 用整数；GraphQL ID 是 GID
  （`gid://axonhub/Model/23`）。
