# Build audit

Checks performed while assembling the clean publication repository:

- Ten publication scripts are included (`00` through `09`); historical and exploratory scripts are excluded.
- The pipeline now starts from the authorized raw `SportoweTalenty2025.csv`; no pre-built project DuckDB database is required.
- `00_build_database.py` reconstructs the one-row-per-student base and applies the latest registered Beep rule.
- Publication scripts do not export individual-level `student_id` audit lists to `results/`.
- No absolute Windows paths (`C:\\...`) remain.
- No e-mail addresses, API-key patterns, passwords or tokens are intended to be present in tracked code/documentation.
- No individual-level data files are included.
- Raw/source/derived data directories and generated results are excluded by `.gitignore`.
- HFZ cut-points are documented in `config/hfz_cutpoints.csv` and loaded by `02_classify_hfz.py`.
- Package versions are pinned in `requirements.txt`.

The complete `00`–`09` pipeline still requires one final execution on the protected manuscript export before public release. In particular, the newly explicit raw-export reconstruction step must reproduce the exact control totals and downstream manuscript results.
