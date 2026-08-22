# AxonHub Models Mapping

自动化模型映射和配置系统，包含数据获取、映射计算和 AxonHub 配置应用。

## 项目结构

```
models-mapping/
├── models-mapping/          # 数据获取和映射计算（Mac 运行）
│   ├── SKILL.md
│   ├── scripts/
│   │   ├── fetch_data.py        # 获取 opencode 和 arena 数据
│   │   └── compute_mapping.py   # 计算模型映射
│   └── references/              # 缓存数据和配置
│
├── axonhub-config/          # AxonHub 配置应用（Server 运行）
│   ├── SKILL.md
│   ├── scripts/
│   │   ├── apply_mapping.py       # 应用映射到 AxonHub
│   │   ├── configure_channels.py  # 配置 channels
│   │   └── configure_models.py    # 配置模型关联
│   └── references/                # 配置文档
│
└── .github/workflows/
    └── fetch.yml            # GitHub Actions 自动化数据获取
```

## 工作流程

### 1. 数据获取（Mac 或 GitHub Actions）

```bash
# 获取数据
cd models-mapping
python3 scripts/fetch_data.py

# 计算映射
python3 scripts/compute_mapping.py --stdout
```

### 2. 应用配置（Server）

```bash
# 预览映射
cd axonhub-config
python3 scripts/apply_mapping.py --axonhub-url <URL> --token <JWT> --dry-run

# 应用映射
python3 scripts/apply_mapping.py --axonhub-url <URL> --token <JWT>
```

## 数据源

- **OpenCode 模型列表**: 从 GitHub 仓库的 `.mdx` 文件解析
- **Arena Leaderboard**: 从 lmarena.ai 爬取（需要 Mac 运行，避免 Cloudflare）

## 自动化

GitHub Actions 每天自动运行数据获取流程：
- 时间：UTC 01:37（北京 09:37）
- 触发：定时 + 手动触发
- 输出：更新 `models-mapping/references/` 下的缓存数据

## 技能引用

这两个 skill 原本位于 `~/.agents/skills/`，现已移动到本项目。如需保持向后兼容，可以创建符号链接：

```bash
ln -s /Users/jason/Documents/workspace/models-mapping/models-mapping ~/.agents/skills/models-mapping
ln -s /Users/jason/Documents/workspace/models-mapping/axonhub-config ~/.agents/skills/axonhub-config
```
