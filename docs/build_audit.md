# Build audit

Checks performed while assembling and testing the clean publication repository:

- Ten publication scripts are included (`00` through `09`); historical and exploratory scripts are excluded.
- The pipeline starts from the authorized raw `SportoweTalenty2025.csv`; no pre-built DuckDB database is required.
- `00_build_database.py` reconstructs the one-row-per-student base from the raw export.
- Repeated 20mSRT records use the latest registered Beep result, with `form_id` as deterministic tie-breaker.
- Conflicting valid source sex codes are resolved using the earliest recorded valid sex code; 29 source student identifiers had such conflicts.
- TERYT municipality codes are normalized to seven characters before deriving voivodeship, preventing loss of leading zeroes for codes beginning `02`, `04`, `06`, or `08`.
- All 16 voivodeships and all 320 voivodeship × age × sex cells are recovered in the regional analyses.
- Publication scripts do not export individual-level `student_id` audit lists to `results/`.
- No individual-level data files are included in the repository.
- Raw and derived data directories and generated results are excluded by `.gitignore`.
- HFZ cut-points are documented in `config/hfz_cutpoints.csv` and loaded by `02_classify_hfz.py`.
- The regional analysis uses one fixed national age-by-sex standard; legacy A/B naming has been removed.
- Package versions are pinned in `requirements.txt`.
- Comments, control messages, README content, and publication documentation are in English; original source field names and Polish voivodeship values are retained where required by the data specification.
- All ten Python scripts pass syntax compilation.

## End-to-end validation on the protected 2025 export

The complete `00`–`09` pipeline was executed successfully on the authorized manuscript export during the repository audit. Key reproduced control totals include:

- source students: 3,000,210;
- eligible students aged 10–19 with valid sex code: 2,969,503;
- final HFZ sample: 2,715,127;
- girls in final HFZ sample: 1,298,369;
- boys in final HFZ sample: 1,416,758;
- achieving HFZ: 1,340,221;
- overall HFZ: 49.361%;
- 16 voivodeships and 320 regional age × sex cells;
- national directly standardized HFZ: 48.837797%;
- repeated Beep records: 71,703 of 2,742,606 students with at least one Beep record (2.6144%).

Because the raw registry export cannot be redistributed, independent execution of the full pipeline requires authorized access to the same data structure.
