# Domain Docs

This project uses a **single-context** domain doc layout.

## Layout

```
/
├── AGENTS.md                     # 常驻约束与分层口径
├── CONTEXT.md                    # Domain glossary
├── .agents/skills/
│   ├── watch-pipeline/           # 采集层：SKILL.md + scripts + reference/
│   └── axonhub-admin/            # 规划与写入层：SKILL.md（大纲）+ scripts/tests + references/（任务流程）
└── docs/
    ├── agents/                   # Agent configuration (this directory)
    └── research/                 # Research notes
```

## Consumer Rules

- **CONTEXT.md**: Read before any design work. Use its canonical terms for
  providers, model cards, request models, candidate models, mappings and the
  requests list.
- **Skills' SKILL.md**: the process source of truth — collection in
  `watch-pipeline/SKILL.md`, compute/write governance and task flows in
  `axonhub-admin/SKILL.md` plus its `references/` task documents.
- **Boundary changes**: update the owning skill's `SKILL.md` and `CONTEXT.md`
  first, then `AGENTS.md` (see AGENTS.md 何时读什么).
- **Updates**: Use `domain-modeling` skill to update CONTEXT.md.
