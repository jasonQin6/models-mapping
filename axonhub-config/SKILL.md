---
name: axonhub-config
description: Apply model mappings and configure AxonHub channels/associations on the server.
---

# axonhub-config

Applies model mappings from upstream CSV and configures AxonHub channels/associations.

**Runs on server only.** Upstream data fetching runs on Mac via `models-mapping`.

## Workflow: Apply Mapping

1. `python3 scripts/apply_mapping.py --axonhub-url <URL> --token <JWT> --dry-run`
   - Completion: preview shows template creation + association updates
   - Verify mapping is correct

2. `python3 scripts/apply_mapping.py --axonhub-url <URL> --token <JWT>`
   - Completion: template created/updated, associations configured, old templates cleaned
   - Verify no errors in output

## Quick Reference

| Operation | Command | Trigger |
|-----------|---------|---------|
| Apply mapping (dry-run) | `python3 scripts/apply_mapping.py --axonhub-url <URL> --token <JWT> --dry-run` | before applying |
| Apply mapping | `python3 scripts/apply_mapping.py --axonhub-url <URL> --token <JWT>` | after user confirms mapping |
| Configure channels | `python3 scripts/configure_channels.py --axonhub-url <URL> --token <JWT>` | when channel config changes |
| Configure model associations | `python3 scripts/configure_models.py --axonhub-url <URL> --token <JWT>` | when non-fixed model routing changes |

## CSV Format

Reads CSV in the format produced by `models-mapping`. Only `request_model` and `target_model` columns are used for mapping; other columns are for human review.

## Error Handling

| Error | Action |
|-------|--------|
| Model not found in AxonHub | Check model ID; may need to create model first |
| Template creation failed | Check JWT token; may be expired |
| Association update failed | Check channel ID; may not exist |

## References

- [Channel configuration](references/channels.md)
- [Non-fixed model associations](references/associations.md)
- [Migration between instances](references/migration.md)
