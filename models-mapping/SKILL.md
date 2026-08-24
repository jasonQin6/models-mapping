---
name: models-mapping
description: Fallback handler for models.csv data gaps. Use when arena_score, rp5h, usage_quota, or price_output columns contain empty values.
---

# models-mapping

处理 models.csv 中的数据缺失问题。当自动化脚本无法填充某些列时，agent 介入判断。

## 触发条件

运行以下检查，如果输出非空，则需要介入：

```bash
python3 -c "
import csv
with open('models.csv') as f:
    reader = csv.DictReader(f)
    for row in reader:
        empty_cols = [k for k in ['arena_score', 'rp5h', 'usage_quota', 'price_output'] if not row.get(k)]
        if empty_cols:
            print(f\"{row['model_id']}: {', '.join(empty_cols)}\")
"
```

## 处理流程

1. **识别空值列**：运行上述检查，列出所有空值
2. **判断原因**：
   - arena_score 空：arena 排行榜无此模型
   - rp5h/usage_quota 空：opencode 文档未提供
   - price_output 空：opencode 文档未提供
3. **应用规则**：根据下方规则填写
4. **验证**：重新运行检查，确认无空值

## Fallback 规则

### arena_score 空值

**策略**：寻找相似模型的 arena 数据

1. **版本降级**：qwen3.7-plus → qwen3.6-plus（降低小版本号）
2. **去除后缀**：muse-spark-1.2-contributor → muse-spark-1.2
3. **前缀匹配**：claude-haiku → claude-haiku-*（选择评分最高的变体）
4. **Free 模型**：arena_score = 0

**判断依据**：
- 版本降级：适用于有明确版本号的模型（qwen3.7 → qwen3.6）
- 去除后缀：适用于 contributor/preview 等变体
- 前缀匹配：适用于同一系列的不同 effort 级别

### rp5h/usage_quota 空值

**策略**：使用同类模型的中位数

1. 找出相同 provider 的模型
2. 计算 rp5h 和 usage_quota 的中位数
3. 填写中位数值

**例外**：
- Free 模型：rp5h = max(其他模型), usage_quota = max(其他模型)
- 新模型：如果无同类参考，标记为待人工确认

### price_output 空值

**策略**：从 opencode 文档获取

1. 检查 go.mdx 是否有此模型的定价信息
2. 如果文档未提供，标记为待人工确认

**注意**：price_output 是加权计算的关键字段，缺失会导致映射结果不准确。

## 完成标准

- 所有模型的 arena_score, rp5h, usage_quota, price_output 列非空
- 填写的值符合上述规则
- 重新运行检查脚本，输出为空

## 参考

- 评分公式：`data/formula.md`
- 自动化脚本：`scripts/watch_arena.py`（已实现 fallback 策略 1-4）
- 数据源：`go.mdx`（opencode 官方文档）
