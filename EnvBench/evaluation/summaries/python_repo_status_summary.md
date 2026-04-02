# Python baseline status summary

Success criterion: `exit_code == 0` and `issues_count == 0`.

| variant | approach | success | failure | missing_vs_union | total_rows | union_total |
|---|---|---:|---:|---:|---:|---:|
| 4o | bash_agent | 22 | 305 | 2 | 327 | 329 |
| 4o | installamatic | 16 | 313 | 0 | 329 | 329 |
| 4o | zero_shot | 18 | 311 | 0 | 329 | 329 |
| 4o-mini | bash_agent | 18 | 309 | 2 | 327 | 329 |
| 4o-mini | installamatic | 9 | 320 | 0 | 329 | 329 |
| 4o-mini | zero_shot | 15 | 314 | 0 | 329 | 329 |

Missing entries in `bash_agent`:

- `4o`: `brightway-lca/brightway2-io@e315f08801fc928bc586f0004b07cf9d651b567d`, `commaai/comma10k@7a1293562fd8c0f3932c2c55f6e8a509efa1c66d`
- `4o-mini`: `brightway-lca/brightway2-io@e315f08801fc928bc586f0004b07cf9d651b567d`, `online-ml/river@6995f1522a9136fd3982890afac5c4a178e4cd57`
