# Build audit

Checks performed when assembling the clean publication repository:

- Nine publication scripts selected; historical and exploratory scripts excluded.
- All nine Python scripts pass syntax compilation (`py_compile`).
- No absolute Windows paths (`C:\\...`) remain.
- No e-mail addresses, API-key patterns, passwords or tokens were detected in tracked text/code files.
- No individual-level data files are included.
- Raw/source/derived data directories and generated results are excluded by `.gitignore`.
- HFZ cut-points are documented in `config/hfz_cutpoints.csv` and loaded by `02_classify_hfz.py`.

The full pipeline was **not executed in this clean copy**, because the restricted individual-level source data are intentionally not included. Final numerical reproduction should therefore be checked once against the authorized local data before public release.
