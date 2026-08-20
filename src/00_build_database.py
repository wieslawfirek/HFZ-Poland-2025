from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd


# =============================================================================
# SETTINGS
# =============================================================================

ROOT = Path.cwd()
RAW_CSV = ROOT / "data" / "raw" / "SportoweTalenty2025.csv"

OUT_DIR = ROOT / "data" / "derived"
REPORT_DIR = ROOT / "results" / "00_build_database"
OUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_DB = OUT_DIR / "sportowe_talenty_2025_base.duckdb"
OUTPUT_TABLE = "uczniowie2025_base"

SUMMARY_CSV = REPORT_DIR / "source_database_reconstruction_summary.csv"
CONTROL_TXT = REPORT_DIR / "source_database_reconstruction_control.txt"

# Exact control totals for the 2025 export used in the manuscript.
EXPECTED_STUDENTS = 3_000_210
EXPECTED_BEEP_RECORDS = 2_823_183
EXPECTED_BEEP_STUDENTS = 2_742_606
EXPECTED_REPEATED_BEEP_STUDENTS = 71_703
EXPECTED_MAX_BEEP_RECORDS = 23


# =============================================================================
# HELPERS
# =============================================================================

def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def registration_timestamp_sql(column: str = "data_rejestracji") -> str:
    """Parse the registration date/time used to identify the latest record."""
    c = qident(column)
    return f"""
        COALESCE(
            TRY_STRPTIME(NULLIF(TRIM({c}), ''), '%d.%m.%Y %H:%M:%S'),
            TRY_STRPTIME(NULLIF(TRIM({c}), ''), '%d.%m.%Y %H:%M'),
            TRY_STRPTIME(NULLIF(TRIM({c}), ''), '%d.%m.%Y'),
            TRY_STRPTIME(NULLIF(TRIM({c}), ''), '%Y-%m-%d %H:%M:%S'),
            TRY_STRPTIME(NULLIF(TRIM({c}), ''), '%Y-%m-%d')
        )
    """


def voivodeship_case(code_expr: str) -> str:
    """Map the first two digits of the TERYT municipality code to voivodeship."""
    return f"""
        CASE LEFT({code_expr}, 2)
            WHEN '02' THEN 'dolnośląskie'
            WHEN '04' THEN 'kujawsko-pomorskie'
            WHEN '06' THEN 'lubelskie'
            WHEN '08' THEN 'lubuskie'
            WHEN '10' THEN 'łódzkie'
            WHEN '12' THEN 'małopolskie'
            WHEN '14' THEN 'mazowieckie'
            WHEN '16' THEN 'opolskie'
            WHEN '18' THEN 'podkarpackie'
            WHEN '20' THEN 'podlaskie'
            WHEN '22' THEN 'pomorskie'
            WHEN '24' THEN 'śląskie'
            WHEN '26' THEN 'świętokrzyskie'
            WHEN '28' THEN 'warmińsko-mazurskie'
            WHEN '30' THEN 'wielkopolskie'
            WHEN '32' THEN 'zachodniopomorskie'
            ELSE NULL
        END
    """


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print("=" * 100)
    print("RECONSTRUCTING THE STUDENT-LEVEL 2025 ANALYTIC BASE FROM THE RAW EXPORT")
    print("Rule for repeated 20mSRT records: latest registered Beep result")
    print("=" * 100)

    if not RAW_CSV.exists():
        raise FileNotFoundError(
            f"Raw registry export not found: {RAW_CSV}\n"
            "Place the authorized file at data/raw/SportoweTalenty2025.csv."
        )

    raw_sql = f"""
        read_csv(
            {sql_string(RAW_CSV.as_posix())},
            delim=';',
            header=true,
            all_varchar=true,
            ignore_errors=false
        )
    """

    # Read the header before starting the expensive aggregation.
    header_con = duckdb.connect()
    columns = (
        header_con.execute(f"DESCRIBE SELECT * FROM {raw_sql}")
        .df()["column_name"]
        .astype(str)
        .tolist()
    )
    header_con.close()

    required = {
        "form_id",
        "student_id",
        "proba",
        "wynik",
        "data_ur",
        "data_rejestracji",
        "plec",
        "kod_ter_gmina",
    }
    missing = sorted(required - set(columns))
    if missing:
        raise RuntimeError(
            "The raw CSV is missing required columns: "
            + ", ".join(missing)
            + "\nAvailable columns: "
            + ", ".join(columns)
        )

    if OUTPUT_DB.exists():
        OUTPUT_DB.unlink()

    reg_ts = registration_timestamp_sql()

    # The raw_core CTE is explicitly materialized for the duration of this query.
    # It is NOT stored in OUTPUT_DB; only the one-row-per-student table is persisted.
    con = duckdb.connect(str(OUTPUT_DB))

    create_sql = f"""
        CREATE TABLE {qident(OUTPUT_TABLE)} AS

        WITH raw_core AS MATERIALIZED (
            SELECT
                TRIM(CAST(student_id AS VARCHAR)) AS student_id,
                LOWER(TRIM(CAST(proba AS VARCHAR))) AS proba_norm,
                TRIM(CAST(wynik AS VARCHAR)) AS wynik_raw,
                LOWER(TRIM(CAST(plec AS VARCHAR))) AS plec_norm,
                CASE
                    WHEN NULLIF(TRIM(CAST(kod_ter_gmina AS VARCHAR)), '') IS NULL THEN NULL
                    ELSE LPAD(TRIM(CAST(kod_ter_gmina AS VARCHAR)), 7, '0')
                END AS kod_ter_gmina,
                NULLIF(TRIM(CAST(data_rejestracji AS VARCHAR)), '') AS data_rejestracji_raw,
                {reg_ts} AS registration_ts,
                TRY_CAST(NULLIF(TRIM(CAST(form_id AS VARCHAR)), '') AS BIGINT) AS form_id_num
            FROM {raw_sql}
            WHERE student_id IS NOT NULL
              AND TRIM(CAST(student_id AS VARCHAR)) <> ''
        ),

        students AS (
            SELECT DISTINCT student_id
            FROM raw_core
        ),

        earliest_sex AS (
            SELECT
                student_id,
                plec_norm AS plec
            FROM raw_core
            WHERE plec_norm IN ('dz', 'ch')
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY student_id
                ORDER BY registration_ts ASC NULLS LAST,
                         form_id_num ASC NULLS LAST
            ) = 1
        ),

        latest_gmina AS (
            SELECT
                student_id,
                kod_ter_gmina
            FROM raw_core
            WHERE kod_ter_gmina IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY student_id
                ORDER BY registration_ts DESC NULLS LAST,
                         form_id_num DESC NULLS LAST
            ) = 1
        ),

        profile_quality AS (
            SELECT
                student_id,
                COUNT(DISTINCT plec_norm) FILTER (
                    WHERE plec_norm IN ('dz', 'ch')
                ) AS n_distinct_valid_sex_values,
                COUNT(DISTINCT kod_ter_gmina) FILTER (
                    WHERE kod_ter_gmina IS NOT NULL
                ) AS n_distinct_gmina_values
            FROM raw_core
            GROUP BY 1
        ),

        beep_rows AS (
            SELECT
                student_id,
                TRY_CAST(
                    REPLACE(NULLIF(TRIM(wynik_raw), ''), ',', '.')
                    AS DOUBLE
                ) AS beep,
                wynik_raw AS beep_wynik_raw,
                data_rejestracji_raw AS beep_data_rejestracji,
                registration_ts,
                form_id_num
            FROM raw_core
            WHERE proba_norm = 'beep'
        ),

        beep_counts AS (
            SELECT
                student_id,
                COUNT(*) AS n_beep_records
            FROM beep_rows
            GROUP BY 1
        ),

        latest_beep AS (
            SELECT
                student_id,
                beep,
                beep_wynik_raw,
                beep_data_rejestracji,
                form_id_num AS beep_form_id
            FROM beep_rows
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY student_id
                ORDER BY registration_ts DESC NULLS LAST,
                         form_id_num DESC NULLS LAST
            ) = 1
        )

        SELECT
            s.student_id,
            sx.plec,
            gm.kod_ter_gmina,
            {voivodeship_case('gm.kod_ter_gmina')} AS wojewodztwo,
            b.beep,
            b.beep_wynik_raw,
            b.beep_data_rejestracji,
            b.beep_form_id,
            COALESCE(bc.n_beep_records, 0)::INTEGER AS n_beep_records,
            COALESCE(pq.n_distinct_valid_sex_values, 0)::INTEGER
                AS n_distinct_valid_sex_values,
            COALESCE(pq.n_distinct_gmina_values, 0)::INTEGER
                AS n_distinct_gmina_values
        FROM students AS s
        LEFT JOIN earliest_sex AS sx USING (student_id)
        LEFT JOIN latest_gmina AS gm USING (student_id)
        LEFT JOIN latest_beep AS b USING (student_id)
        LEFT JOIN beep_counts AS bc USING (student_id)
        LEFT JOIN profile_quality AS pq USING (student_id)
    """

    con.execute(create_sql)

    try:
        con.execute(
            f"CREATE INDEX idx_base_student_id ON {qident(OUTPUT_TABLE)} (student_id)"
        )
    except Exception:
        pass

    n_students = int(
        con.execute(f"SELECT COUNT(*) FROM {qident(OUTPUT_TABLE)}").fetchone()[0]
    )
    n_unique = int(
        con.execute(
            f"SELECT COUNT(DISTINCT student_id) FROM {qident(OUTPUT_TABLE)}"
        ).fetchone()[0]
    )

    beep_control = con.execute(
        f"""
        SELECT
            SUM(n_beep_records)::BIGINT AS n_beep_records,
            SUM(CASE WHEN n_beep_records > 0 THEN 1 ELSE 0 END)::BIGINT
                AS n_students_with_beep,
            SUM(CASE WHEN n_beep_records > 1 THEN 1 ELSE 0 END)::BIGINT
                AS n_students_with_repeated_beep,
            MAX(n_beep_records)::INTEGER AS max_beep_records_per_student,
            SUM(CASE WHEN beep IS NULL AND n_beep_records > 0 THEN 1 ELSE 0 END)::BIGINT
                AS n_latest_beep_not_numeric,
            SUM(CASE WHEN n_distinct_valid_sex_values > 1 THEN 1 ELSE 0 END)::BIGINT
                AS n_students_with_conflicting_sex,
            SUM(CASE WHEN n_distinct_gmina_values > 1 THEN 1 ELSE 0 END)::BIGINT
                AS n_students_with_multiple_gmina_codes
        FROM {qident(OUTPUT_TABLE)}
        """
    ).df()

    b = beep_control.iloc[0]
    n_beep_records = int(b["n_beep_records"])
    n_beep_students = int(b["n_students_with_beep"])
    n_repeated = int(b["n_students_with_repeated_beep"])
    max_repeated = int(b["max_beep_records_per_student"])
    n_latest_non_numeric = int(b["n_latest_beep_not_numeric"])

    checks = {
        "students": (n_students, EXPECTED_STUDENTS),
        "unique_students": (n_unique, EXPECTED_STUDENTS),
        "raw_beep_records": (n_beep_records, EXPECTED_BEEP_RECORDS),
        "students_with_beep": (n_beep_students, EXPECTED_BEEP_STUDENTS),
        "students_with_repeated_beep": (n_repeated, EXPECTED_REPEATED_BEEP_STUDENTS),
        "max_beep_records_per_student": (max_repeated, EXPECTED_MAX_BEEP_RECORDS),
        "latest_beep_not_numeric": (n_latest_non_numeric, 0),
    }

    failed = [name for name, (observed, expected) in checks.items() if observed != expected]

    summary_rows = [
        {"metric": name, "observed": observed, "expected": expected,
         "status": "OK" if observed == expected else "MISMATCH"}
        for name, (observed, expected) in checks.items()
    ]
    summary_rows.extend([
        {
            "metric": "students_with_conflicting_sex",
            "observed": int(b["n_students_with_conflicting_sex"]),
            "expected": None,
            "status": "AUDIT",
        },
        {
            "metric": "students_with_multiple_gmina_codes",
            "observed": int(b["n_students_with_multiple_gmina_codes"]),
            "expected": None,
            "status": "AUDIT",
        },
    ])
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(SUMMARY_CSV, index=False, encoding="utf-8-sig")

    control_lines = [
        "STUDENT-LEVEL DATABASE RECONSTRUCTION FROM RAW SPORTOWE TALENTY 2025 EXPORT",
        "=" * 96,
        "",
        f"Raw CSV: {RAW_CSV}",
        f"Output DB: {OUTPUT_DB}",
        f"Output table: {OUTPUT_TABLE}",
        "",
        "REPEATED 20mSRT RULE:",
        "For students with more than one Beep record, the latest registered record is selected.",
        "Registration date/time is the primary ordering variable; form_id is the deterministic tie-breaker.",
        "",
        "DEMOGRAPHIC FIELDS:",
        "Sex is taken from the earliest recorded valid sex code; municipality code is taken from the latest available valid record.",
        "Voivodeship is derived from the first two digits of the TERYT municipality code.",
        "",
        summary.to_string(index=False),
        "",
        "PRIVACY:",
        "No individual-level audit list is written to results/. The derived DuckDB file remains local and is ignored by Git.",
    ]
    CONTROL_TXT.write_text("\n".join(control_lines), encoding="utf-8")

    con.close()

    if failed:
        raise RuntimeError(
            "The reconstructed database does not match the control totals for the manuscript export. "
            "Failed checks: " + ", ".join(failed)
            + f"\nSee: {CONTROL_TXT}"
        )

    print("\nRECONSTRUCTION COMPLETE")
    print(summary.to_string(index=False))
    print(f"\nDatabase: {OUTPUT_DB}")
    print(f"Control report: {CONTROL_TXT}")
    print("=" * 100)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nERROR:", exc)
        sys.exit(1)
