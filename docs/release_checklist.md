# Public-release checklist

Before making the GitHub repository public:

- [ ] Confirm that `data/raw/`, `data/source/`, `data/derived/`, and `results/` contain no tracked restricted files.
- [ ] Confirm that no individual-level CSV, Parquet, DuckDB, Excel or database export has been added.
- [ ] Run `00_build_database.py` on the authorized raw export and confirm that all reconstruction control totals are `OK`.
- [ ] Run the complete pipeline (`00` through `09`) on the authorized data.
- [ ] Compare the headline values with the submitted manuscript: source N, eligible N, final N, overall HFZ, sex-specific HFZ, regional range, IPW and MNAR results.
- [ ] Confirm that the manuscript’s final HFZ cut-points match `config/hfz_cutpoints.csv`.
- [ ] Confirm that no script writes individual-level identifier lists to tracked output locations.
- [ ] Add a repository URL/DOI to the manuscript Data/Code Availability statement after archiving a release.
- [ ] Choose a software license before public release if code reuse is intended.
- [ ] Optionally archive the accepted version on Zenodo and cite the release DOI.
