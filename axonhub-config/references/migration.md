# Migration Between Instances

## Export models from source

```bash
curl -s -X POST <SOURCE>/admin/graphql \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"query": "query { models(first: 50) { edges { node { modelID name developer type icon group remark modelCard } } } }"}' \
  > /tmp/models-export.json
```

## Import models to target

```python
# Read export, create each model on target via GraphQL
# mutation: createModel(input: {modelID, name, developer, type, icon, group, modelCard, ...})
# Skip if "already exists" error
```

## Associations

Channel associations are operational state and should be reconciled through
`configure_models.py` after inspecting its dry-run. Fixed Claude/GPT request
mapping is a separate, user-confirmed operation owned by the
[`models-mapping`](../../models-mapping/SKILL.md) skill; it is not part of a
general instance migration.
