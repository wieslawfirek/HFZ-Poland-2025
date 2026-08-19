# Provenance of publication scripts

This clean repository was assembled from the working research repository. Historical versions and exploratory analyses were intentionally excluded.

| Publication script | Provenance |
|---|---|
| `00_build_database.py` | Publication-clean reconstruction step added to make the analytic pipeline start from the authorized raw 2025 registry export rather than from a pre-built project DuckDB database. It implements the final one-student/one-row rule and latest repeated Beep rule used for the manuscript. |
| `01_prepare_age.py` | Based on `utworz_baze_wiek_FitnessGram_2025.py`, modified to consume the locally reconstructed base from `00_build_database.py`. |
| `02_classify_hfz.py` | Based on `przelicz_HFZ_2025_po_nowym_wieku.py`; publication version does not export individual student identifiers in audit files. |
| `03_validate_20msrt.py` | Based on `walidacja_20mSRT_2025.py`. |
| `04_missingness.py` | Based on `analiza_kompletnosci_i_selekcji_20mSRT_2025.py`. |
| `05_age_sex_model.py` | Publication-clean implementation of the final grouped-binomial age × sex analysis. |
| `06_decomposition_14_15.py` | Based on `dekompozycja_HFZ_14_15_2025.py`. |
| `07_regional_standardization.py` | Based on `standaryzacja_regionalna_HFZ_2025_A_vs_B.py`. |
| `08_ipw_mnar.py` | Based on `analiza_IPW_MNAR_2025.py`. |
| `09_repeated_records.py` | Based on `policz_wielokrotne_rekordy_20mSRT_2025.py`; publication version reports aggregate frequencies without exporting student identifiers. |

The new `00_build_database.py` step must be numerically validated against the previously used protected administrative ETL before public release. The script contains exact aggregate control totals to make that comparison explicit.
