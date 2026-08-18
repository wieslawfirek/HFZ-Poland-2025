# Input data specification

## Raw CSV: `data/raw/SportoweTalenty2025.csv`

Expected semicolon-delimited UTF-8 file with the following source fields:

| Field | Description |
|---|---|
| `form_id` | Registry form identifier. |
| `student_id` | Student identifier used to reconcile records. |
| `class_id` | Class identifier in the source export (not used as a school identifier). |
| `user_id` | Source-system user identifier. |
| `proba` | Test name; 20mSRT records are identified as `beep`. |
| `wynik` | Recorded test result. For 20mSRT, number of completed 20-m shuttles. |
| `data_ur` | Date of birth. |
| `data_rejestracji` | Registration date/time of the record. |
| `plec` | Sex code (`dz` girls, `ch` boys in the analytic source). |
| `kod_ter_gmina` | TERYT municipality code. |

## Student-level source database

`data/source/sportowe_talenty.duckdb` must contain table `uczniowie2025_hfz`. This is the project’s student-level administrative ETL output, with one record per student and the selected 20mSRT result (`beep`) when available.

The publication pipeline subsequently derives:

- `wiek_fitnessgram` — completed age on 30 April 2025;
- `status_wieku_fitnessgram` — age-resolution status;
- `prog_hfz_fitnessgram` — HFZ cut-point in completed shuttles;
- `hfz_fitnessgram_nowy` — binary HFZ classification;
- `wojewodztwo` — voivodeship used for regional analyses.

## Important limitations

The available source database does not contain a school identifier. Municipality-level geography cannot be used to reconstruct school clustering.
