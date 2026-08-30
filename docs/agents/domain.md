# Domain Docs

This project uses a **single-context** domain doc layout.

## Layout

```
/
├── CONTEXT.md          # Domain glossary
├── docs/
│   ├── adr/            # Architecture Decision Records (5 ADRs)
│   └── agents/         # Agent configuration (this directory)
```

## Consumer Rules

- **CONTEXT.md**: Read before any design work. Use its canonical terms for
  providers, model cards, request models, candidate models, mappings and the
  mapping workspace.
- **docs/adr/**: Read when making architectural decisions. ADR-0005 is the
  current source of truth for separated source snapshots and confirmed
  AxonHub writes; ADR-0001 through ADR-0004 are superseded history.
- **Updates**: Use `domain-modeling` skill to update CONTEXT.md or create new ADRs.
