# Non-Fixed Model Associations

`configure_models.py` handles channel associations for non-claude/gpt models (glm, kimi, qwen, etc.). Priority is based on channel quota and billing type.

```bash
python3 scripts/configure_models.py --axonhub-url <URL> --token <JWT> --dry-run
python3 scripts/configure_models.py --axonhub-url <URL> --token <JWT>
```

## Priority logic

- Count-based channels first (negative score)
- Token-based: `100000 - quota`
- Adjustments: +10000 for opencode-go deepseek/qwen, -5000 for Ali-Token night models
