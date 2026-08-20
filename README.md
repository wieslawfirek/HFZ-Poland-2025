# HFZ Poland 2025 — analysis code

This repository contains the analysis code supporting the manuscript on Healthy Fitness Zone (HFZ) attainment among Polish schoolchildren using the 2025 **Sportowe Talenty** registry export.

## Scope

This is a publication-oriented analysis repository. It contains the code required to reconstruct the student-level analytic dataset from the authorized raw registry export and to reproduce the analyses reported in the manuscript. Individual-level data, derived databases, exploratory notebooks, temporary files, and historical script versions are intentionally excluded.

## Data availability

The individual-level registry data are **not included** in this repository. The data are administered by the Polish Ministry of Sport and Tourism, and the authors are not authorized to redistribute them.

A researcher with authorized access needs only the original long-format 2025 registry export described below. The repository reconstructs the required DuckDB files locally; no pre-built `.duckdb` database is required.

## Required local input

Place the authorized raw export at:

`data/raw/SportoweTalenty2025.csv`

The expected file is a semicolon-delimited long-format export containing at least the following source fields: `form_id`, `student_id`, `proba`, `wynik`, `data_ur`, `data_rejestracji`, `plec`, and `kod_ter_gmina`.

These field names are retained because they are the original registry variable names. Their English meanings are documented in `docs/data_dictionary.md`.

The raw export and all locally derived individual-level files are excluded by `.gitignore` and must not be committed to GitHub.

## Analysis sequence

Run the scripts from the repository root in this order:

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

## Pipeline overview

| Script | Purpose |
|---|---|
| `00_build_database.py` | Reconstructs a one-row-per-student analytic base from the raw export. For repeated 20mSRT records, the latest registered Beep result is retained. Conflicting valid sex codes are resolved using the earliest recorded valid sex code. Municipality codes are taken from the latest available valid record. TERYT municipality codes are normalized to seven characters before deriving voivodeship. |
| `01_prepare_age.py` | Reconciles date of birth across all records for each student and computes completed age on 30 April 2025. |
| `02_classify_hfz.py` | Applies age- and sex-specific FITNESSGRAM/PACER HFZ cut-points and creates the final HFZ analysis database. |
| `03_validate_20msrt.py` | Describes the continuous 20mSRT distribution and audits extreme, non-integer, and heaped values. |
| `04_missingness.py` | Quantifies population coverage and missing 20mSRT results by age, sex, and voivodeship. |
| `05_age_sex_model.py` | Fits the grouped-binomial age × sex model to 20 aggregated age-by-sex cells. |
| `06_decomposition_14_15.py` | Decomposes the difference in HFZ attainment between ages 14 and 15 into score-distribution and threshold components. |
| `07_regional_standardization.py` | Performs direct regional standardization using one common national age-by-sex standard. |
| `08_ipw_mnar.py` | Performs IPW, cell-level positivity checks, and MNAR sensitivity analyses. |
| `09_repeated_records.py` | Quantifies repeated raw 20mSRT records. |

## Student-level reconstruction rules

`00_build_database.py` creates the local database:

`data/derived/sportowe_talenty_2025_base.duckdb`

with table `uczniowie2025_base`.

The reconstruction applies the following deterministic rules:

- one row per `student_id`;
- if more than one `beep` record exists, the **latest registered** record is selected;
- registration date/time is the primary ordering variable and `form_id` is the deterministic tie-breaker;
- if conflicting valid sex codes occur across records for the same student, the **earliest recorded valid sex code** is retained;
- municipality code is taken from the latest available valid record;
- municipality TERYT codes are normalized to seven characters using left zero-padding before the first two digits are used to derive voivodeship;
- no individual-level audit list is written to `results/`.

The 2025 export used for the manuscript contained 29 students with conflicting valid sex codes across source records. The deterministic rule above reproduces the final manuscript sex counts. This audit does not imply that these records represent verified errors in true sex; it only documents inconsistent source coding across repeated records.

## Age definition

Age is defined as the number of completed years on **30 April 2025**.

For each `student_id`, all non-empty date-of-birth values (`data_ur`) are examined:

- exactly one unique valid date → accepted;
- no valid date → age remains unresolved;
- more than one valid date → treated as a date-of-birth conflict and age remains unresolved;
- no majority-rule imputation is used;
- registration date is not used to calculate age.

## HFZ classification

HFZ is classified from the 20m shuttle run using the age- and sex-specific FITNESSGRAM/PACER cut-points stored in:

`config/hfz_cutpoints.csv`

The final analytical sample is restricted to students aged 10–19 years with a valid sex code and an available 20mSRT result.

## Regional standardization

Regional estimates use **one common standard population**: the national age-by-sex distribution of all eligible students aged 10–19 years in the export, before exclusion for missing 20mSRT results (`N = 2,969,503`).

For each voivodeship, HFZ proportions are calculated in 20 age-by-sex cells and directly standardized using the fixed national cell weights.

For the standardized proportion,

`P_std = sum_j(w_j * p_j)`

and

`Var(P_std) = sum_j(w_j^2 * p_j * (1-p_j) / n_j)`.

The 95% CI is calculated as the estimate ± 1.96 SE.

## Age × sex model

The age × sex analysis is fitted to aggregated counts of students achieving and not achieving HFZ in **20 age-by-sex cells**, using a grouped-binomial response. BIC is intentionally not reported for this grouped-binomial presentation.

## Missing-data sensitivity analyses

Stabilized IPW is defined as:

`P(R=1) / P(R=1 | age, sex, voivodeship, age×sex)`

with weights truncated at the 1st and 99th percentiles.

A complementary 320-cell weighting analysis uses voivodeship × age × sex cells. Because these weights are constant within the same cells used for regional standardization, unchanged standardized regional estimates after IPW are an expected mathematical property and should not be interpreted as independent evidence of robustness.

MNAR scenarios assume that students with missing 20mSRT results have HFZ probabilities lower than observed students in the same voivodeship × age × sex cell by 0, 5, 10, or 20 percentage points. After applying the deficit, probabilities are constrained to `[0,1]`.

## Repeated 20mSRT records

The raw export contains 2,823,183 Beep records from 2,742,606 students with at least one Beep record. More than one Beep record occurs for 71,703 students (2.6144%). The publication pipeline reports only aggregate counts and does not export student identifiers for these cases.

## Software

The final analysis environment is documented in `requirements.txt`. The pipeline was tested using Python 3.14.6 and the pinned package versions listed there.

## Outputs and privacy

Generated analysis outputs are written to `results/`. Derived DuckDB and Parquet files are written to `data/derived/`. These locations are ignored by Git.

The repository therefore provides **code reproducibility conditional on authorized access to the raw registry export**. The restricted individual-level data are not redistributed.

## Repository status

The complete 00–09 pipeline was run successfully from the authorized raw 2025 export during the publication-repository audit. Historical and exploratory scripts are intentionally omitted.
