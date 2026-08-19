# HFZ Poland 2025 — analysis code

This repository contains the analysis code supporting the manuscript on Healthy Fitness Zone (HFZ) attainment among Polish schoolchildren using the 2025 **Sportowe Talenty** registry export.

## Scope

The repository is a publication-oriented copy of the analytic workflow. It contains only the scripts needed to reconstruct the student-level analytic base from the authorized raw export and to reproduce the manuscript analyses. It intentionally excludes individual-level data, derived databases, temporary files, exploratory notebooks, and historical versions of scripts.

## Data availability

The individual-level data are **not included** in this repository. They are administered by the Polish Ministry of Sport and Tourism. The authors are not authorized to redistribute the individual-level data.

A researcher with authorized access needs only the original long-format 2025 registry export described below. The publication code reconstructs the required student-level DuckDB database locally; no pre-built `.duckdb` file is required.

## Required local input (not tracked by Git)

Place the authorized raw export at:

`data/raw/SportoweTalenty2025.csv`

The expected file is a semicolon-delimited long-format export with fields including `form_id`, `student_id`, `proba`, `wynik`, `data_ur`, `data_rejestracji`, `plec`, and `kod_ter_gmina`. See `docs/data_dictionary.md`.

The raw export and all locally derived databases are excluded by `.gitignore` and must not be committed to a public repository.

## Analysis sequence

Run scripts from the repository root in the following order:

```bash
python src/00_build_database.py
python src/01_prepare_age.py
python src/02_classify_hfz.py
python src/03_validate_20msrt.py
python src/04_missingness.py
python src/05_age_sex_model.py
python src/06_decomposition_14_15.py
python src/07_regional_standardization.py
python src/08_ipw_mnar.py
python src/09_repeated_records.py
```

### What each script does

| Script | Purpose |
|---|---|
| `00_build_database.py` | Reconstructs the one-row-per-student analytic base directly from the raw registry export and selects the latest registered 20mSRT (`beep`) result when repeated records exist. |
| `01_prepare_age.py` | Reconciles date of birth at student level and computes completed age on 30 April 2025. |
| `02_classify_hfz.py` | Applies age- and sex-specific FITNESSGRAM/PACER HFZ cut-points and creates the final HFZ analysis database. |
| `03_validate_20msrt.py` | Describes the continuous 20mSRT distribution, extremes and heaping. |
| `04_missingness.py` | Quantifies export coverage and missing 20mSRT results by age, sex and voivodeship. |
| `05_age_sex_model.py` | Fits the grouped-binomial age × sex model and creates Tables S4–S6 and Figure 2 inputs. |
| `06_decomposition_14_15.py` | Decomposes the age-14 vs age-15 HFZ difference into score-distribution and threshold components. |
| `07_regional_standardization.py` | Performs direct age/sex standardization by voivodeship and compares alternative standards. |
| `08_ipw_mnar.py` | Performs IPW, cell-based positivity checks and MNAR sensitivity scenarios. |
| `09_repeated_records.py` | Quantifies students with more than one raw 20mSRT record. |

## Reconstruction of the student-level base

`00_build_database.py` uses the raw long-format export to create a local file:

`data/derived/sportowe_talenty_2025_base.duckdb`

with table `uczniowie2025_base`.

The script applies the following manuscript-relevant rules:

- one row per `student_id`;
- when more than one `beep` record exists, the **latest registered** record is selected;
- registration date/time is the primary ordering variable and `form_id` is used as a deterministic tie-breaker;
- sex and municipality code are taken from the latest available valid record for each field;
- voivodeship is derived from the first two digits of the TERYT municipality code;
- aggregate control totals are checked against the exact 2025 export used for the manuscript.

No individual-level audit list is written to `results/`.

## Key analytic conventions

- Age is defined as **completed years on 30 April 2025**.
- Date of birth is reconciled from all available raw records for each student; registration date is not used to calculate age.
- HFZ is classified using the age- and sex-specific 20m PACER cut-points listed in `config/hfz_cutpoints.csv`.
- Regional estimates are directly standardized to the national age × sex distribution of all eligible students present in the export before exclusion for missing 20mSRT.
- Stabilized IPW uses `P(R=1) / P(R=1|X)` and is truncated at the 1st and 99th percentiles.
- MNAR scenarios constrain assumed probabilities to the interval `[0,1]` after applying the specified deficit.

## Confidence intervals

- Cell proportions use the normal binomial approximation.
- Differences between independent proportions use the square root of the sum of component variances.
- For directly standardized regional proportions, the variance is calculated as `sum(w_j^2 * p_j * (1-p_j) / n_j)` with fixed standard-population weights, and 95% CIs are estimate ± 1.96 SE.

## Software

The final environment is documented in `requirements.txt`. The analysis was run in Python 3.14.6 with pinned package versions.

## Outputs

Generated files are written to `results/`. Local derived DuckDB/Parquet files are written to `data/derived/`. Both locations are ignored by Git so that restricted or generated files are not committed accidentally.

## Reproducibility note

Because the individual-level registry export cannot be redistributed, this repository provides **full code reproducibility conditional on authorized access to the raw source export**. A pre-built project database is not required: the publication pipeline reconstructs the analytic database from `SportoweTalenty2025.csv`.

## Repository status

This publication repository was created from a larger working repository. Historical scripts, exploratory notebooks and analyses not used in the manuscript were intentionally omitted.
