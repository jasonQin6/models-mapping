# 批量（重）建模型

在目录被清空或迁移后需要批量重建时阅读；单条模型写入走 SKILL.md 的执行循环。
工具：graphql-cli（机制见 axonhub-cli skill），大结果读取可用带 JWT 的 curl。

## 调用纪律

- mutation 的输入必须用 **variables** 传（`-v '{"input": …}'`）；内联 JSON 对象字面量是
  非法 GraphQL（`Expected Name, found String`）。
- 变量必须按 mutation 形状嵌套：更新类是 `{"id": …, "input": {…}}`。把 `modelCard`/
  `remark` 放在变量顶层会报 `must be defined`（HTTP 422）——服务端整体拒绝，什么都没写。
- **CLI 报错有两种形态，检测必须同时覆盖**：`Error: HTTP 422: {"errors":[…]}`（CLI 打在
  输出里的服务端拒绝）与响应 JSON 内的 `"errors"`。只 grep `"errors"` 会把失败当成功
  （实例：37 条 updateModel 全部"成功"，实际全被 422 拒绝，回读才暴露）。
- **CLI 输出有噪音且不可靠**（npm notice、多段 JSON 拼接）：mutation 可能服务端成功而本地
  解析报失败，反之亦然。每轮之后与线上状态对账（`models(first: 100)`），只补真正缺失的部分。
- **先对账后执行**：上一次运行可能已部分落库（含被取消的运行）。从线上状态重算"剩余工作"，
  绝不重放已完成的写入。
- **修改没有 bulk mutation**：批量改成本/备注/卡片就是逐条 `updateModel` 循环（npx 每次约
  2s 启动开销）。创建用 `bulkCreateModels(inputs: […])`，启用用 `bulkEnableModels(ids)`。
- **整体替换**：`modelCard` 输入会替换整张卡——先读全卡，改目标字段，整体回写；只传 `cost`
  会把 reasoning/toolCall/modalities 等全部清成零值。

## 软删除与清理

- 软删除的行会阻塞创建：删除后的模型对 `models` 不可见但仍占着 ID，`createModel` 报
  `model name 'x' already exists`。可用 `node(id: "gid://axonhub/Model/<n>") { … on Model { modelID } }`
  探测（node 查询绕过软删拦截器），按 ID 升序探测可重建完整清单。
- 用 `deleteModel(id)` 清除——resolver 上下文跳过软删拦截器，是硬删。
  `bulkDeleteModels(ids)` 返回 `true` 但观察为**不**清理。每次清理以"随后的 createModel
  成功"验证，从不信返回值。
- **建后启用**：`createModel` 与 Web UI 建出的模型都是 disabled，用
  `updateModelStatus(id, enabled)` / `bulkEnableModels(ids)` 翻转。
- 关联接线是建完后的独立一轮（payload 里 `settings: {associations: []}`）；请求模型路由
  再按映射表走。

## Payload 约定（每模型，卡片数据来自 `data/all_models.json`）

- `developer` — models.dev 的厂商前缀归一化为 AxonHub 英文厂商词表：
  `zai-org`/`zhipuai` → `zai`，`meituan` → `longcat`，`moonshotai` → `moonshot`，
  `deepseek-ai` → `deepseek`。
- `icon` — 按厂商给 lobe-icons 名（DeepSeek、ChatGLM、Qwen、Moonshot、XAI、Hunyuan、
  LongCat、XiaomiMiMo、Meta、NVIDIA、Step、Gemini、OpenAI）；不确定就空串。
- `group` — 卡片的 `family`。
- `type` — `chat`；图像生成端点（如 `sensenova-u1-fast`，`POST /v1/images/generations`，
  无图像输入、非 Chat Completions）是 `image_generation`，modalities `input: [text]` /
  `output: [image]`、`vision: false`，并靠 `models_extra.json` 记录上的 `exclude` 旗标
  排除出聊天注册表。
- `modelCard` — `reasoning: {supported, default}` ← `reasoning`；`toolCall` ← `tool_call`；
  `temperature` ← `temperature`（默认 true）；`vision` ← modalities.input 里的 `image`；
  `modalities`、`limit` 照搬；`cost` ← `{input, output, cacheRead: cache_read, cacheWrite: cache_write}`；
  `knowledge`、`releaseDate` ← `release_date`、`lastUpdated`（存在时）。
- `settings` — `{associations: []}`；可选策略字段省略（部署怪癖，见 SKILL.md）。
- 成本优先级：渠道声明值（`data/models_extra.json`）逐字段覆盖卡片值；`free: true` 视为
  渠道声明全部为零价（详见 [models.md](models.md)）。
