# Provenance of publication scripts

This clean repository was assembled from the larger working research repository. Historical versions and exploratory analyses were intentionally excluded.

| Publication script | Provenance / publication role |
|---|---|
| `00_build_database.py` | Publication reconstruction step that makes the pipeline start from the authorized raw 2025 registry export. It implements the final one-student/one-row rule, latest repeated Beep rule, earliest-valid-sex rule for conflicting source sex codes, and seven-character TERYT normalization. |
| `01_prepare_age.py` | Based on the final age-reconciliation workflow; modified to consume the locally reconstructed base from `00_build_database.py`. |
| `02_classify_hfz.py` | Based on the final HFZ recalculation workflow; publication version does not export individual student identifiers in audit files. |
| `03_validate_20msrt.py` | Publication version of the continuous 20mSRT validation and descriptive audit. |
| `04_missingness.py` | Publication version of the completeness and missingness analysis. |
| `05_age_sex_model.py` | Publication-clean implementation of the final grouped-binomial age × sex analysis. |
| `06_decomposition_14_15.py` | Publication version of the age-14 vs age-15 decomposition. |
| `07_regional_standardization.py` | Final direct-standardization script using one national eligible age-by-sex standard. |
| `08_ipw_mnar.py` | Final IPW, positivity, and MNAR sensitivity-analysis script using the same regional standard definition. |
| `09_repeated_records.py` | Publication version reporting aggregate frequencies of repeated 20mSRT records without exporting student identifiers. |

The complete final pipeline was executed on the protected manuscript export and reproduced the manuscript control totals documented in `build_audit.md`.
