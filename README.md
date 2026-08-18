# HFZ Poland 2025 — analysis code

This repository contains the analysis code supporting the manuscript on Healthy Fitness Zone (HFZ) attainment among Polish schoolchildren using the 2025 **Sportowe Talenty** registry export.

## Scope

The repository is a publication-oriented copy of the analytic workflow. It contains only the scripts needed for the manuscript analyses. It intentionally excludes individual-level data, derived databases, temporary files, exploratory notebooks, and historical versions of scripts.

## Data availability

The individual-level data are **not included** in this repository. They are administered by the Polish Ministry of Sport and Tourism. The authors are not authorized to redistribute the individual-level data. Researchers with authorized access can place the required files in the local `data/` directories described below.

## Required local inputs (not tracked by Git)

Two restricted inputs are expected:

1. `data/source/sportowe_talenty.duckdb`  
   Expected table: `uczniowie2025_hfz`. This is the student-level source database produced by the administrative ETL used in the project.
2. `data/raw/SportoweTalenty2025.csv`  
   Raw long-format export with columns including `student_id`, `proba`, `wynik`, `data_ur`, `data_rejestracji`, `plec`, and `kod_ter_gmina`.

The raw export is used only where record-level audit information is required (e.g., reconciliation of date of birth and frequency of repeated 20mSRT records).

## Analysis sequence

Run scripts from the repository root in the following order:

```bash
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
| `01_prepare_age.py` | Reconciles date of birth at student level and computes completed age on 30 April 2025. |
| `02_classify_hfz.py` | Applies age- and sex-specific FITNESSGRAM/PACER HFZ cut-points and creates the final HFZ analysis database. |
| `03_validate_20msrt.py` | Describes the continuous 20mSRT distribution, extremes and heaping. |
| `04_missingness.py` | Quantifies source-database coverage and missing 20mSRT results by age, sex and voivodeship. |
| `05_age_sex_model.py` | Fits the grouped-binomial age × sex model and creates Tables S4–S6 and Figure 2 inputs. |
| `06_decomposition_14_15.py` | Decomposes the age-14 vs age-15 HFZ difference into score-distribution and threshold components. |
| `07_regional_standardization.py` | Performs direct age/sex standardization by voivodeship and compares alternative standards. |
| `08_ipw_mnar.py` | Performs IPW, cell-based positivity checks and MNAR sensitivity scenarios. |
| `09_repeated_records.py` | Quantifies students with more than one raw 20mSRT record. |

## Key analytic conventions

- Age is defined as **completed years on 30 April 2025**.
- HFZ is classified using the age- and sex-specific 20m PACER cut-points listed in `config/hfz_cutpoints.csv`.
- When more than one 20mSRT record exists for the same student, the analytic source database uses the **latest registered result** according to the registry processing rule.
- Regional estimates are directly standardized to the national age × sex distribution of all eligible students present in the export before exclusion for missing 20mSRT.
- Stabilized IPW uses `P(R=1) / P(R=1|X)` and is truncated at the 1st and 99th percentiles.
- MNAR scenarios constrain assumed probabilities to the interval `[0,1]` after applying the specified deficit.

## Confidence intervals

- Cell proportions use the normal binomial approximation.
- Differences between independent proportions use the square root of the sum of component variances.
- For directly standardized regional proportions, the variance is calculated as `sum(w_j^2 * p_j * (1-p_j) / n_j)` with fixed standard-population weights, and 95% CIs are estimate ± 1.96 SE.

## Software

The analysis was developed in Python 3.14. The main packages are DuckDB, pandas, NumPy, SciPy, statsmodels, patsy, matplotlib and openpyxl. See `requirements.txt`.

## Outputs

Generated files are written to `results/`. The directory is ignored by Git so that locally generated tables, figures and audit files are not committed accidentally.

## Reproducibility note

Because the individual-level registry data cannot be redistributed, this repository provides **analytic-code reproducibility conditional on authorized access to the source data**. Expected input structure is documented in `docs/data_dictionary.md`.

## Repository status

This publication repository was created from a larger working repository. Historical scripts, exploratory notebooks and analyses not used in the manuscript were intentionally omitted.
