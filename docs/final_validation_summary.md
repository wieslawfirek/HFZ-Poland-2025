# Final validation summary

The final publication pipeline was executed end-to-end on the authorized 2025 registry export before release preparation.

## Reproduced control totals

- Source students: **3,000,210**
- Students with resolved age: **2,997,515**
- Students aged 10–19 with valid sex code: **2,969,503**
- Missing 20mSRT among eligible students: **254,376 (8.566%)**
- Final HFZ analytical sample: **2,715,127**
- Girls: **1,298,369**
- Boys: **1,416,758**
- Achieving HFZ: **1,340,221**
- Overall HFZ: **49.361%**
- Voivodeships: **16**
- Voivodeship × age × sex cells: **320**
- National directly standardized HFZ: **48.837797%**
- Highest standardized regional HFZ: **małopolskie, 52.969852%**
- Lowest standardized regional HFZ: **świętokrzyskie, 43.147940%**
- Standardized regional range: **9.821913 percentage points**
- Logistic IPW estimate after truncation: **48.868221%**
- Repeated Beep records: **71,703 / 2,742,606 students (2.6144%)**

## Source-data audits incorporated into the final pipeline

- 29 student identifiers contained conflicting valid sex codes across source records. A deterministic earliest-recorded-valid-code rule is used.
- TERYT municipality codes are left-padded to seven characters before voivodeship derivation, preserving leading zeroes.
- The raw export contains one non-integer 20mSRT value (117.01); it remains in the source-derived data and does not affect its HFZ classification.

These checks document reproducibility of the code on the protected manuscript export. Individual-level data are not included in the repository.
