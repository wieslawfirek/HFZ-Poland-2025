# Local data directories

Individual-level registry data are not distributed with this repository.

## Required restricted input

Place the authorized raw registry export at:

- `raw/SportoweTalenty2025.csv` — raw long-format 2025 **Sportowe Talenty** export.

No pre-built DuckDB database is required. `src/00_build_database.py` reconstructs the one-row-per-student analytic base locally and writes it to `derived/`.

## Local derived files

The pipeline creates DuckDB/Parquet files in `derived/`. These files may contain individual-level information and must remain local.

The contents of `data/raw/`, `data/source/` (retained as a defensively ignored legacy location), and `data/derived/` are excluded from Git.

**Do not commit individual-level registry data or derived individual-level databases to a public repository.**
