# Manuscript-to-code mapping

| Manuscript element | Script / output |
|---|---|
| Raw export → one-row-per-student analytic base; latest repeated 20mSRT rule | `00_build_database.py` |
| Participant flow and corrected age | `01_prepare_age.py`, `02_classify_hfz.py` |
| 20mSRT descriptive validation / heaping | `03_validate_20msrt.py` |
| Missing 20mSRT by age, sex, region | `04_missingness.py` |
| Age × sex logistic model; Tables S4–S6; Figure 2 | `05_age_sex_model.py` |
| Age 14–15 decomposition | `06_decomposition_14_15.py` |
| Regional standardization; Figure 3 | `07_regional_standardization.py` |
| IPW, positivity and MNAR | `08_ipw_mnar.py` |
| Frequency of repeated 20mSRT records | `09_repeated_records.py` |

The regional-standardization script uses the same fixed-weight variance formula that underlies the 95% CIs shown for standardized regional percentages.
