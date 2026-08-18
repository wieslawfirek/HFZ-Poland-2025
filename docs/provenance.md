# Provenance of publication scripts

This clean repository was assembled from the working repository. The following final working scripts were retained and renamed:

| Publication script | Working-repository source |
|---|---|
| `01_prepare_age.py` | `utworz_baze_wiek_FitnessGram_2025.py` |
| `02_classify_hfz.py` | `przelicz_HFZ_2025_po_nowym_wieku.py` |
| `03_validate_20msrt.py` | `walidacja_20mSRT_2025.py` |
| `04_missingness.py` | `analiza_kompletnosci_i_selekcji_20mSRT_2025.py` |
| `05_age_sex_model.py` | publication-clean implementation of the final grouped-binomial age × sex analysis |
| `06_decomposition_14_15.py` | `dekompozycja_HFZ_14_15_2025.py` |
| `07_regional_standardization.py` | `standaryzacja_regionalna_HFZ_2025_A_vs_B.py` |
| `08_ipw_mnar.py` | `analiza_IPW_MNAR_2025.py` |
| `09_repeated_records.py` | `policz_wielokrotne_rekordy_20mSRT_2025.py` |

Historical versions, exploratory BMI/ML analyses, notebooks, maps, generic ETL utilities and old age-calculation scripts were intentionally excluded.
