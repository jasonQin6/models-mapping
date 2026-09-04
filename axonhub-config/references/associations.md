# Non-fixed model channel associations

`configure_models.py` handles channel associations for models that are not the
fixed Claude/GPT request set (for example glm, kimi and qwen). These are
`channel_model` associations used for channel routing; they are separate from
the one-to-one request mapping written by `models-mapping` as a `type=model`
association.

```bash
AXONHUB_JWT=<JWT> python3 axonhub-config/scripts/configure_models.py --axonhub-url <URL> --dry-run
AXONHUB_JWT=<JWT> python3 axonhub-config/scripts/configure_models.py --axonhub-url <URL>
```

## Priority logic

- Count-based channels first (negative score)
- Token-based: `100000 - quota`
- Adjustments: +10000 for opencode-go deepseek/qwen, -5000 for Ali-Token night models

The script skips Claude/GPT request models. Inspect its dry-run output before
applying channel changes, and do not use it to replace a confirmed request
mapping.
