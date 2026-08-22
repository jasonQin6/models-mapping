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

## Import associations (legacy)

Previously done via `import_associations.py` + `models.json`. Now handled by `apply_mapping.py` reading CSV from models-mapping skill.
