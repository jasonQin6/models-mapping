# Channel Configuration

Configure channel tags (quota) and ordering weights.

```bash
AXONHUB_JWT=<JWT> python3 axonhub-config/scripts/configure_channels.py --axonhub-url <URL> --dry-run
AXONHUB_JWT=<JWT> python3 axonhub-config/scripts/configure_channels.py --axonhub-url <URL>
```

**Tag format:** `quotaXXXX` where XXXX is the per-5h request quota
**Weight range:** 0-10 (10 = highest priority)

## Channel table

Source of truth: `axonhub-config/scripts/common.py` → `CHANNELS`

| ID | Name | Weight | Tag | Quota | Billing |
|----|------|--------|-----|-------|---------|
| 4 | Ali-Coding | 10 | quota6000 | 6000 | count |
| 3 | Sensenova | 9 | quota1500 | 1500 | count |
| 6 | opencode-go | 8 | quota3000 | 45300 | token |
| 7 | opencode-luna | 7 | quota2000 | 2000 | token |
| 2 | GLM | 3 | quota80 | 80 | token |
| 5 | Ali-Token | 2 | quota100 | 100 | token |
| 8 | deepseek | 1 | quota0 | 0 | token |
