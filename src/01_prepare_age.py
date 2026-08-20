from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd


# =============================================================================
# SETTINGS
# =============================================================================

ROOT = Path.cwd()

SOURCE_DB = ROOT / "data" / "derived" / "sportowe_talenty_2025_base.duckdb"
SOURCE_TABLE = "uczniowie2025_base"

REFERENCE_DATE = "2025-04-30"

RAW_CANDIDATES = [
    ROOT / "data" / "raw" / "SportoweTalenty2025.csv",
]

OUT_DIR = ROOT / "data" / "derived"
REPORT_DIR = ROOT / "results" / "01_age"

OUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_DB_STEM = OUT_DIR / "sportowe_talenty_2025_wiek_fitnessgram"
OUTPUT_PARQUET_STEM = OUT_DIR / "uczniowie2025_wiek_fitnessgram"

SUMMARY_CSV = REPORT_DIR / "wiek_fitnessgram_baza_podsumowanie.csv"
AGE_COUNTS_CSV = REPORT_DIR / "wiek_fitnessgram_liczebnosci_wiek_plec.csv"
STATUS_COUNTS_CSV = REPORT_DIR / "wiek_fitnessgram_statusy_przypisania.csv"
CONTROL_TXT = REPORT_DIR / "wiek_fitnessgram_baza_kontrola.txt"

MIN_EXPECTED_N = 100_000


# =============================================================================
# HELPERS
# =============================================================================

def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def unique_output_path(stem: Path, suffix: str) -> Path:
    """
    Does not overwrite an earlier output.
    Pierwszy plik: stem + suffix
    Kolejne: stem_v2 + suffix, stem_v3 + suffix, ...
    """
    first = Path(str(stem) + suffix)
    if not first.exists():
        return first

    version = 2
    while True:
        candidate = Path(str(stem) + f"_v{version}" + suffix)
        if not candidate.exists():
            return candidate
        version += 1


def find_raw_2025() -> Path:
    found = [p for p in RAW_CANDIDATES if p.exists()]

    if not found:
        checked = "\n".join(f"  - {p}" for p in RAW_CANDIDATES)
        raise FileNotFoundError(
            "Raw file SportoweTalenty2025.csv was not found.\n"
            "Checked:\n" + checked
        )

    for p in found:
        if ROOT in p.parents:
            return p

    return found[0]


def choose_column(
    columns: list[str],
    candidates: list[str],
    required: bool = False,
) -> str | None:
    lookup = {c.lower(): c for c in columns}

    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]

    if required:
        raise KeyError(
            "Required column not found. Searched for: "
            + ", ".join(candidates)
            + "\nAvailable columns: "
            + ", ".join(columns)
        )

    return None


def completed_age_sql(birth_expr: str) -> str:
    """
    Number of completed years exactly on 30 April 2025.
    """
    return f"""
        (
            EXTRACT(YEAR FROM DATE '{REFERENCE_DATE}')
            - EXTRACT(YEAR FROM {birth_expr})
            - CASE
                WHEN STRFTIME(DATE '{REFERENCE_DATE}', '%m%d')
                     < STRFTIME({birth_expr}, '%m%d')
                THEN 1
                ELSE 0
              END
        )::INTEGER
    """


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print("=" * 100)
    print("CREATING DATABASE WITH FITNESSGRAM AGE")
    print(f"Reference date: {REFERENCE_DATE}")
    print("=" * 100)

    if not SOURCE_DB.exists():
        raise FileNotFoundError(
            f"Input database not found: {SOURCE_DB}\n"
            "Run first: python .\\src\\00_build_database.py"
        )

    raw_path = find_raw_2025()

    output_db = unique_output_path(OUTPUT_DB_STEM, ".duckdb")
    output_parquet = unique_output_path(OUTPUT_PARQUET_STEM, ".parquet")

    print(f"Source database: {SOURCE_DB}")
    print(f"Source table: {SOURCE_TABLE}")
    print(f"Raw CSV: {raw_path}")
    print(f"New database: {output_db}")
    print(f"New Parquet: {output_parquet}")

    # The new database is written to a separate file; the source database is not modified.
    con = duckdb.connect(str(output_db))

    con.execute(
        f"ATTACH {sql_string(SOURCE_DB.as_posix())} AS src (READ_ONLY)"
    )

    tables = (
        con.execute("SHOW TABLES FROM src")
        .df()["name"]
        .astype(str)
        .tolist()
    )

    if SOURCE_TABLE not in tables:
        con.close()
        raise RuntimeError(
            f"Table not found: {SOURCE_TABLE} w bazie {SOURCE_DB}.\n"
            "Available tables: " + ", ".join(tables)
        )

    desc = con.execute(
        f"DESCRIBE src.main.{qident(SOURCE_TABLE)}"
    ).df()

    source_columns = desc["column_name"].astype(str).tolist()

    col_id = choose_column(
        source_columns,
        ["student_id", "uczen_id", "uczeń_id", "id_ucznia"],
        required=True,
    )

    col_old_age = choose_column(
        source_columns,
        ["wiek_lata", "age_years", "wiek"],
        required=False,
    )

    col_sex = choose_column(
        source_columns,
        ["plec", "sex", "sex"],
        required=False,
    )

    source_n = int(
        con.execute(
            f"SELECT COUNT(*) FROM src.main.{qident(SOURCE_TABLE)}"
        ).fetchone()[0]
    )

    if source_n < MIN_EXPECTED_N:
        con.close()
        raise RuntimeError(
            f"The source table contains only {source_n:,} records. "
            "The script stopped to avoid running on test data."
        )

    # -------------------------------------------------------------------------
    # Raw CSV
    # -------------------------------------------------------------------------

    raw_sql = f"""
        read_csv(
            {sql_string(raw_path.as_posix())},
            delim=';',
            header=true,
            all_varchar=true,
            ignore_errors=false
        )
    """

    raw_columns = (
        con.execute(f"DESCRIBE SELECT * FROM {raw_sql}")
        .df()["column_name"]
        .astype(str)
        .tolist()
    )

    required_raw = {"student_id", "data_ur"}
    missing_raw = required_raw - set(raw_columns)

    if missing_raw:
        con.close()
        raise RuntimeError(
            "W surowym CSV brakuje kolumn: "
            + ", ".join(sorted(missing_raw))
            + "\nAvailable columns: "
            + ", ".join(raw_columns)
        )

    # -------------------------------------------------------------------------
    # Date-of-birth parsing
    #
    # NOTE:
    # - data_rejestracji is NOT used to calculate age;
    # - data_ur is treated as a student-level attribute;
    # - all records for each student_id are used.
    # -------------------------------------------------------------------------

    parsed_birth = """
        COALESCE(
            TRY_STRPTIME(NULLIF(TRIM(data_ur), ''), '%d.%m.%Y')::DATE,
            TRY_STRPTIME(NULLIF(TRIM(data_ur), ''), '%Y-%m-%d')::DATE,
            TRY_CAST(NULLIF(TRIM(data_ur), '') AS DATE)
        )
    """

    birth_profile_sql = f"""
        SELECT
            TRIM(CAST(student_id AS VARCHAR)) AS student_id_join,

            COUNT(*) AS n_raw_records,

            COUNT(DISTINCT {parsed_birth}) AS n_distinct_valid_birth_dates,

            MIN({parsed_birth}) AS unique_birth_date_candidate,

            STRING_AGG(
                DISTINCT CAST({parsed_birth} AS VARCHAR),
                ' | '
                ORDER BY CAST({parsed_birth} AS VARCHAR)
            ) FILTER (WHERE {parsed_birth} IS NOT NULL)
                AS valid_birth_dates

        FROM {raw_sql}
        WHERE student_id IS NOT NULL
          AND TRIM(CAST(student_id AS VARCHAR)) <> ''
        GROUP BY 1
    """

    resolved_birth_sql = f"""
        WITH profile AS (
            {birth_profile_sql}
        )
        SELECT
            student_id_join,
            n_raw_records,
            n_distinct_valid_birth_dates,
            valid_birth_dates,

            CASE
                WHEN n_distinct_valid_birth_dates = 1
                    THEN unique_birth_date_candidate
                ELSE NULL
            END AS birth_date_final,

            CASE
                WHEN n_distinct_valid_birth_dates = 1
                    THEN 'assigned_unique_birth_date'
                WHEN n_distinct_valid_birth_dates = 0
                    THEN 'missing_birth_date'
                WHEN n_distinct_valid_birth_dates > 1
                    THEN 'conflicting_birth_dates'
                ELSE 'unresolved'
            END AS birth_date_status

        FROM profile
    """

    # -------------------------------------------------------------------------
    # Create new table
    # -------------------------------------------------------------------------

    new_age_sql = completed_age_sql("r.birth_date_final")

    if col_old_age:
        old_age_completed_sql = (
            f"FLOOR(TRY_CAST(t.{qident(col_old_age)} AS DOUBLE))::INTEGER"
        )
    else:
        old_age_completed_sql = "NULL::INTEGER"

    if col_sex:
        sex_sql = f"CAST(t.{qident(col_sex)} AS VARCHAR)"
    else:
        sex_sql = "NULL::VARCHAR"

    # Legacy HFZ variables are retained only for audit purposes.
    # The new age variable is explicitly named and must be used in the next step.
    create_table_sql = f"""
        CREATE TABLE uczniowie2025_wiek_fitnessgram AS

        WITH resolved_birth AS (
            {resolved_birth_sql}
        )

        SELECT
            t.*,

            r.birth_date_final AS data_urodzenia_final,

            DATE '{REFERENCE_DATE}' AS data_referencyjna_fitnessgram,

            {new_age_sql} AS wiek_fitnessgram,

            r.birth_date_status AS status_daty_urodzenia,

            r.n_distinct_valid_birth_dates AS liczba_roznych_dat_urodzenia,

            r.valid_birth_dates AS daty_urodzenia_audyt,

            CASE
                WHEN r.student_id_join IS NULL
                    THEN 'student_id_not_found_in_raw'
                WHEN r.birth_date_status = 'assigned_unique_birth_date'
                    THEN 'wiek_przypisany'
                WHEN r.birth_date_status = 'missing_birth_date'
                    THEN 'brak_daty_urodzenia'
                WHEN r.birth_date_status = 'conflicting_birth_dates'
                    THEN 'konflikt_dat_urodzenia'
                ELSE 'wiek_nierozstrzygniety'
            END AS status_wieku_fitnessgram,

            CASE
                WHEN {new_age_sql} BETWEEN 10 AND 19
                    THEN 1
                ELSE 0
            END AS wiek_fitnessgram_10_19,

            {old_age_completed_sql}
                AS wiek_dotychczasowy_pelne_lata,

            CASE
                WHEN r.birth_date_final IS NULL
                  OR {old_age_completed_sql} IS NULL
                    THEN NULL
                ELSE ({new_age_sql}) - ({old_age_completed_sql})
            END AS roznica_wieku_vs_dotychczas,

            {sex_sql} AS plec_audyt

        FROM src.main.{qident(SOURCE_TABLE)} AS t

        LEFT JOIN resolved_birth AS r
            ON TRIM(CAST(t.{qident(col_id)} AS VARCHAR))
             = r.student_id_join
    """

    con.execute(create_table_sql)

    # -------------------------------------------------------------------------
    # Optional index
    # -------------------------------------------------------------------------

    try:
        con.execute(
            f"""
            CREATE INDEX idx_wiek_fg_student_id
            ON uczniowie2025_wiek_fitnessgram ({qident(col_id)})
            """
        )
    except Exception:
        # The index is not required for correctness.
        pass

    # -------------------------------------------------------------------------
    # View for subsequent analysis of ages 10–19
    # -------------------------------------------------------------------------

    con.execute(
        """
        CREATE VIEW uczniowie2025_wiek_fitnessgram_10_19 AS
        SELECT *
        FROM uczniowie2025_wiek_fitnessgram
        WHERE status_wieku_fitnessgram = 'wiek_przypisany'
          AND wiek_fitnessgram BETWEEN 10 AND 19
        """
    )

    # -------------------------------------------------------------------------
    # Parquet export
    # -------------------------------------------------------------------------

    con.execute(
        f"""
        COPY (
            SELECT *
            FROM uczniowie2025_wiek_fitnessgram
        )
        TO {sql_string(output_parquet.as_posix())}
        (
            FORMAT PARQUET,
            COMPRESSION ZSTD
        )
        """
    )

    # -------------------------------------------------------------------------
    # SUMMARY
    # -------------------------------------------------------------------------

    summary = con.execute(
        """
        SELECT
            COUNT(*) AS n_wszystkie,

            SUM(
                CASE WHEN status_wieku_fitnessgram = 'wiek_przypisany'
                THEN 1 ELSE 0 END
            ) AS n_wiek_przypisany,

            SUM(
                CASE WHEN status_wieku_fitnessgram = 'brak_daty_urodzenia'
                THEN 1 ELSE 0 END
            ) AS n_brak_daty_urodzenia,

            SUM(
                CASE WHEN status_wieku_fitnessgram = 'konflikt_dat_urodzenia'
                THEN 1 ELSE 0 END
            ) AS n_konflikt_dat_urodzenia,

            SUM(
                CASE WHEN status_wieku_fitnessgram = 'student_id_not_found_in_raw'
                THEN 1 ELSE 0 END
            ) AS n_student_id_not_found_in_raw,

            SUM(
                CASE WHEN wiek_fitnessgram BETWEEN 10 AND 19
                THEN 1 ELSE 0 END
            ) AS n_wiek_10_19,

            SUM(
                CASE
                    WHEN roznica_wieku_vs_dotychczas = 0
                    THEN 1 ELSE 0
                END
            ) AS n_wiek_bez_zmiany,

            SUM(
                CASE
                    WHEN roznica_wieku_vs_dotychczas = -1
                    THEN 1 ELSE 0
                END
            ) AS n_nowy_wiek_o_1_mniejszy,

            SUM(
                CASE
                    WHEN roznica_wieku_vs_dotychczas = 1
                    THEN 1 ELSE 0
                END
            ) AS n_nowy_wiek_o_1_wiekszy,

            SUM(
                CASE
                    WHEN roznica_wieku_vs_dotychczas IS NOT NULL
                     AND roznica_wieku_vs_dotychczas NOT IN (-1, 0, 1)
                    THEN 1 ELSE 0
                END
            ) AS n_inna_zmiana_wieku

        FROM uczniowie2025_wiek_fitnessgram
        """
    ).df()

    summary.to_csv(
        SUMMARY_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    status_counts = con.execute(
        """
        SELECT
            status_wieku_fitnessgram,
            COUNT(*) AS n,
            ROUND(
                100.0 * COUNT(*) / SUM(COUNT(*)) OVER (),
                4
            ) AS procent
        FROM uczniowie2025_wiek_fitnessgram
        GROUP BY 1
        ORDER BY n DESC
        """
    ).df()

    status_counts.to_csv(
        STATUS_COUNTS_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    # -------------------------------------------------------------------------
    # Counts by new age and sex
    # -------------------------------------------------------------------------

    age_counts = con.execute(
        """
        SELECT
            wiek_fitnessgram AS wiek,
            plec_audyt AS plec,
            COUNT(*) AS n
        FROM uczniowie2025_wiek_fitnessgram
        WHERE status_wieku_fitnessgram = 'wiek_przypisany'
          AND wiek_fitnessgram BETWEEN 10 AND 19
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).df()

    age_counts.to_csv(
        AGE_COUNTS_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    # -------------------------------------------------------------------------
    # Date-of-birth conflicts
    # -------------------------------------------------------------------------

    # For data-protection reasons, the publication repository does not export
    # student_id lists for conflicting dates of birth. The number of such
    # cases remains available in summary tables and the control report.
    # Detailed records remain only in the protected local database
    # under data/derived, which is excluded from version control.

    # -------------------------------------------------------------------------
    # Integrity check
    # -------------------------------------------------------------------------

    new_n = int(
        con.execute(
            "SELECT COUNT(*) FROM uczniowie2025_wiek_fitnessgram"
        ).fetchone()[0]
    )

    if new_n != source_n:
        con.close()
        raise RuntimeError(
            f"Integrity error: source has {source_n:,} rows, "
            f"a nowa tabela {new_n:,}."
        )

    s = summary.iloc[0]

    control_lines = [
        "NOWA BAZA Z WIEKIEM FITNESSGRAM — 2025",
        "=" * 88,
        "",
        f"Source database: {SOURCE_DB}",
        f"Source table: {SOURCE_TABLE}",
        f"Raw file: {raw_path}",
        f"New database: {output_db}",
        f"New Parquet: {output_parquet}",
        "",
        "AGE CRITERION:",
        f"Age = completed years on {REFERENCE_DATE}.",
        "Registration date is NOT used to calculate age.",
        "",
        "DATE-OF-BIRTH RULE:",
        "1. For each student_id, all non-empty data_ur values are examined",
        "   across all records in the raw 2025 file.",
        "2. Exactly one unique valid data_ur -> date accepted.",
        "3. No valid data_ur -> age is not assigned.",
        "4. More than one valid data_ur -> conflict; age is not assigned.",
        "5. No imputation or majority rule is used.",
        "",
        "IMPORTANT:",
        "Legacy HFZ columns from the source table are retained for audit only.",
        "The next stage must use the 'wiek_fitnessgram' column and",
        "reassign HFZ cut-points and status from that age variable.",
        "",
        "SUMMARY:",
        summary.to_string(index=False),
        "",
        "STATUSES:",
        status_counts.to_string(index=False),
        "",
        "INTEGRITY CHECK:",
        f"Rows in source: {source_n:,}",
        f"Liczba wierszy w nowej tabeli: {new_n:,}",
        "Row-count agreement: YES",
        "",
        "OBJECTS IN NEW DATABASE:",
        "Table: uczniowie2025_wiek_fitnessgram",
        "Widok: uczniowie2025_wiek_fitnessgram_10_19",
        "",
        "REPORT FILES:",
        str(SUMMARY_CSV),
        str(STATUS_COUNTS_CSV),
        str(AGE_COUNTS_CSV),
    ]

    CONTROL_TXT.write_text(
        "\n".join(control_lines),
        encoding="utf-8",
    )

    con.close()

    print("\nDONE")
    print(summary.to_string(index=False))
    print("\nNew database:")
    print(output_db)
    print("\nTable:")
    print("uczniowie2025_wiek_fitnessgram")
    print("\nView for ages 10–19:")
    print("uczniowie2025_wiek_fitnessgram_10_19")
    print("\nParquet:")
    print(output_parquet)
    print("\nRaport kontroli:")
    print(CONTROL_TXT)
    print("\nThe original database was NOT modified.")
    print("=" * 100)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nERROR:", exc)
        sys.exit(1)
