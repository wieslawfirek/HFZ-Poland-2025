# Local data directories

The contents of `data/raw/`, `data/source/`, and `data/derived/` are excluded from Git.

Expected restricted inputs:

- `raw/SportoweTalenty2025.csv` — raw long-format 2025 registry export.
- `source/sportowe_talenty.duckdb` — student-level source database containing table `uczniowie2025_hfz`.

Derived DuckDB/Parquet files are created in `derived/` by the analysis scripts.

**Do not commit individual-level registry data to a public repository.**
