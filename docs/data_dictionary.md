# Input data specification

## Raw CSV: `data/raw/SportoweTalenty2025.csv`

The publication pipeline requires only the authorized raw long-format 2025 registry export. The expected file is semicolon-delimited and contains the following source fields:

| Field | Description |
|---|---|
| `form_id` | Registry form identifier; used as a deterministic tie-breaker when records share the same registration date/time. |
| `student_id` | Student identifier used to reconcile records and reconstruct one row per student. |
| `class_id` | Class identifier in the source export; not a school identifier. |
| `user_id` | Source-system user identifier. |
| `proba` | Test name; 20mSRT records are identified as `beep`. |
| `wynik` | Recorded test result. For 20mSRT, the number of completed 20-m shuttles. |
| `data_ur` | Date of birth. |
| `data_rejestracji` | Registration date/time of the record. Used to identify the latest repeated `beep` record, but not to calculate age. |
| `plec` | Source sex code (`dz` girls, `ch` boys). |
| `kod_ter_gmina` | TERYT municipality code. |

Original Polish field names are retained because they are part of the source data specification.

## Locally reconstructed student-level base

`src/00_build_database.py` creates:

- file: `data/derived/sportowe_talenty_2025_base.duckdb`;
- table: `uczniowie2025_base`.

The table contains one row per `student_id` and includes:

| Derived field | Description |
|---|---|
| `plec` | Earliest recorded valid sex code when multiple source records are present. If conflicting valid codes occur, this deterministic rule resolves the conflict. |
| `kod_ter_gmina` | Latest available valid municipality code, normalized to seven characters by left zero-padding when needed. |
| `wojewodztwo` | Voivodeship derived from the first two digits of the normalized TERYT municipality code. |
| `beep` | 20mSRT result selected from the latest registered `beep` record. |
| `beep_data_rejestracji` | Registration date/time of the selected `beep` record. |
| `beep_form_id` | Form identifier of the selected `beep` record. |
| `n_beep_records` | Number of raw `beep` records for the student. |

The 2025 export contains 29 student identifiers with conflicting valid sex codes across source records. The deterministic earliest-valid-code rule reproduces the final manuscript sex counts. This is an audit of source-code inconsistency and does not establish which recorded value, if any, reflects a verified data-entry error.

The reconstruction script also performs aggregate control checks against the manuscript export: 3,000,210 students, 2,823,183 raw Beep records, 2,742,606 students with at least one Beep record, 71,703 students with repeated Beep records, and a maximum of 23 Beep records for one student.

## Subsequent analytic variables

The remaining scripts derive:

- `wiek_fitnessgram` — completed age on 30 April 2025;
- `status_wieku_fitnessgram` — age-resolution status;
- `prog_hfz_fitnessgram` — HFZ cut-point in completed shuttles;
- `hfz_fitnessgram_nowy` — binary HFZ classification.

## Important limitation

The available export does not contain a school identifier. Municipality-level geography cannot be used to reconstruct school clustering.
