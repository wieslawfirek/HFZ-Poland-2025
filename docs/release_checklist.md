# Public-release checklist

Before making the GitHub repository public:

- [ ] Confirm that `data/raw/`, `data/source/`, `data/derived/`, and `results/` contain no tracked files.
- [ ] Confirm that no individual-level CSV, Parquet, DuckDB, Excel or database export has been added.
- [ ] Confirm that the manuscript’s final HFZ cut-points match `config/hfz_cutpoints.csv`.
- [ ] Run the full pipeline on the authorized data and compare headline values with the submitted manuscript.
- [ ] Add a repository URL/DOI to the manuscript Data/Code Availability statement after archiving a release.
- [ ] Choose a software license before public release if code reuse is intended.
- [ ] Optionally archive the accepted version on Zenodo and cite the release DOI.
