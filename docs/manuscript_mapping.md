# Manuscript-to-code mapping

| Manuscript element | Script / output |
|---|---|
| Raw export → one-row-per-student analytic base; repeated 20mSRT rule; deterministic handling of conflicting source sex codes; TERYT normalization | `00_build_database.py` |
| Participant flow and completed age on 30 April 2025 | `01_prepare_age.py`, `02_classify_hfz.py` |
| HFZ classification and age/sex descriptive results | `02_classify_hfz.py` |
| Continuous 20mSRT descriptive validation / heaping | `03_validate_20msrt.py` |
| Missing 20mSRT by age, sex, and voivodeship | `04_missingness.py` |
| Grouped-binomial age × sex model; age/sex CI calculations | `05_age_sex_model.py` |
| Age 14–15 decomposition | `06_decomposition_14_15.py` |
| Direct regional standardization; Figure 3 | `07_regional_standardization.py` |
| IPW, positivity, and MNAR sensitivity analysis | `08_ipw_mnar.py` |
| Frequency of repeated 20mSRT records | `09_repeated_records.py` |

Regional standardization uses one fixed national standard: the age-by-sex distribution of all eligible students aged 10–19 years represented in the export before exclusion for missing 20mSRT results (`N = 2,969,503`).
