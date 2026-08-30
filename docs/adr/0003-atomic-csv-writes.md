---
status: superseded by ADR-0005
---

# Atomic CSV writes with canonical column order

> Historical decision. The former multi-writer CSV pipeline is retired; the
> current artifact boundary is defined by ADR-0005.

All scripts read and write models.csv through the `csv_io` module, which enforces a canonical 21-column order and uses atomic writes (temp file + `os.replace()`).

**Why atomic:** Three scripts (parse_opencode_mdx, watch_arena, compute_target_assignment) update models.csv in a pipeline. A crash mid-write would corrupt the file. Atomic writes prevent partial updates.

**Why canonical order:** Previously, parse_opencode_mdx defined its own column order (17 columns, mapping in position 2), while the actual models.csv had 21 columns with mapping at the end. This drift caused confusion. Now `COLUMNS` in csv_io.py is the single source of truth.

**Why not strict validation:** read_models() accepts any column order and reorders to canonical. Strict validation would break when humans manually edit the CSV. Loose validation (accept any order, output standardized) is more resilient.
