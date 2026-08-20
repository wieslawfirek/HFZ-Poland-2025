# Public-release checklist

Completed during the publication-repository audit:

- [x] Run `00_build_database.py` on the authorized raw export and confirm reconstruction control totals.
- [x] Run the complete pipeline (`00` through `09`) on the authorized data.
- [x] Reproduce source N, eligible N, final N, overall HFZ, final sex counts, 16 voivodeships, and 320 regional cells.
- [x] Confirm the manuscript HFZ cut-points match `config/hfz_cutpoints.csv`.
- [x] Confirm no publication script writes individual-level identifier lists to tracked output locations.
- [x] Confirm the regional analysis uses one common national age-by-sex standard.
- [x] Convert publication-facing comments, messages, and documentation to English while retaining original source field names.

Before making the GitHub repository public:

- [ ] Confirm with `git status` / GitHub that no files under `data/raw/`, `data/source/`, `data/derived/`, or `results/` are tracked.
- [ ] Confirm that no individual-level CSV, Parquet, DuckDB, Excel, or other database export has been committed.
- [ ] Review the final diff after replacing the repository files with this package.
- [ ] Add the repository URL to the manuscript Data and Code Availability statement.
- [ ] Choose a software license before public release if code reuse is intended.
- [ ] Optionally create a versioned GitHub release and archive it in Zenodo to obtain a DOI.
