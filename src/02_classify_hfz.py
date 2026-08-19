from __future__ import annotations

import math
import sys
from pathlib import Path

import duckdb
import pandas as pd


# =============================================================================
# USTAWIENIA
# =============================================================================

ROOT = Path.cwd()
BAZA = ROOT / "data" / "derived"
CUTOFFS_CSV = ROOT / "config" / "hfz_cutpoints.csv"

# Baza utworzona wcześniej przez:
#   src/01_prepare_age.py
INPUT_GLOB = "sportowe_talenty_2025_wiek_fitnessgram*.duckdb"
INPUT_TABLE = "uczniowie2025_wiek_fitnessgram"

OUT_DIR = ROOT / "results" / "02_hfz"
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_DB_BASE = BAZA / "sportowe_talenty_2025_HFZ_po_nowym_wieku.duckdb"
OUTPUT_PARQUET_BASE = BAZA / "uczniowie2025_HFZ_po_nowym_wieku.parquet"

FILE_FLOW = OUT_DIR / "HFZ_2025_przeplyw_kwalifikacji.csv"
FILE_OVERALL = OUT_DIR / "HFZ_2025_ogolem.csv"
FILE_SEX = OUT_DIR / "HFZ_2025_wg_plci.csv"
FILE_AGE = OUT_DIR / "HFZ_2025_wg_wieku.csv"
FILE_AGE_SEX = OUT_DIR / "HFZ_2025_wiek_plec.csv"
FILE_DIFFS = OUT_DIR / "HFZ_2025_roznice_sasiednie_wieki.csv"
FILE_1415 = OUT_DIR / "HFZ_2025_14_15_lat.csv"
FILE_OLD_NEW = OUT_DIR / "HFZ_2025_porownanie_stary_nowy_status.csv"
FILE_CONTROL = OUT_DIR / "HFZ_2025_kontrola.txt"


# =============================================================================
# POMOCNICZE
# =============================================================================

def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def versioned_path(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while True:
        p = path.with_name(f"{path.stem}_v{i}{path.suffix}")
        if not p.exists():
            return p
        i += 1


def choose_column(columns: list[str], candidates: list[str], required=True):
    lookup = {c.lower(): c for c in columns}
    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]
    if required:
        raise KeyError(
            "Nie znaleziono wymaganej kolumny. Szukano: "
            + ", ".join(candidates)
            + "\nDostępne kolumny: "
            + ", ".join(columns)
        )
    return None


def newest_input_db() -> Path:
    candidates = list(BAZA.glob(INPUT_GLOB))
    # Nie bierzemy baz wynikowych HFZ, gdyby nazwy kiedyś się zbliżyły.
    candidates = [
        p for p in candidates
        if "HFZ_po_nowym_wieku" not in p.name
    ]
    if not candidates:
        raise FileNotFoundError(
            "Nie znaleziono bazy z nowym wiekiem.\n"
            f"Szukano w: {BAZA}\\{INPUT_GLOB}\n"
            "Najpierw uruchom: python .\\src\\01_prepare_age.py"
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


def add_ci(df: pd.DataFrame, n_col="n", k_col="n_hfz") -> pd.DataFrame:
    """95% CI dla proporcji: klasyczne przybliżenie normalne.
    Przy N rzędu dziesiątek/setek tysięcy różnice względem Wilsona są minimalne.
    """
    out = df.copy()
    p = out[k_col] / out[n_col]
    se = (p * (1 - p) / out[n_col]).pow(0.5)
    out["hfz_pct"] = (100 * p).round(3)
    out["ci95_low"] = (100 * (p - 1.96 * se).clip(lower=0)).round(3)
    out["ci95_high"] = (100 * (p + 1.96 * se).clip(upper=1)).round(3)
    return out


# =============================================================================
# PROGRAM
# =============================================================================

def main():
    print("=" * 100)
    print("PONOWNE WYLICZENIE HFZ PO NOWYM WIEKU — 2025")
    print("Wiek = pełne ukończone lata na 30.04.2025")
    print("=" * 100)

    input_db = newest_input_db()
    output_db = versioned_path(OUTPUT_DB_BASE)
    output_parquet = versioned_path(OUTPUT_PARQUET_BASE)

    print(f"Baza wejściowa: {input_db}")
    print(f"Baza wynikowa:   {output_db}")

    # Najpierw sprawdzamy strukturę bazy wejściowej.
    src = duckdb.connect(str(input_db), read_only=True)

    tables = src.execute("SHOW TABLES").df()["name"].astype(str).tolist()
    if INPUT_TABLE not in tables:
        src.close()
        raise RuntimeError(
            f"Nie znaleziono tabeli {INPUT_TABLE}.\n"
            "Dostępne obiekty: " + ", ".join(tables)
        )

    columns = (
        src.execute(f"DESCRIBE {qident(INPUT_TABLE)}")
        .df()["column_name"]
        .astype(str)
        .tolist()
    )

    col_student = choose_column(columns, ["student_id"])
    col_age = choose_column(columns, ["wiek_fitnessgram"])
    col_sex = choose_column(columns, ["plec", "płeć", "sex"])
    col_beep = choose_column(columns, ["beep", "pacer", "20msrt"])
    col_age_status = choose_column(
        columns,
        ["status_wieku_fitnessgram"],
        required=False,
    )
    col_old_hfz = choose_column(
        columns,
        ["HFZ_BEEP", "hfz_beep", "hfz"],
        required=False,
    )

    n_source = int(
        src.execute(f"SELECT COUNT(*) FROM {qident(INPUT_TABLE)}").fetchone()[0]
    )
    n_students = int(
        src.execute(
            f"SELECT COUNT(DISTINCT {qident(col_student)}) "
            f"FROM {qident(INPUT_TABLE)}"
        ).fetchone()[0]
    )

    # Zasada 1 uczeń = 1 rekord. Repozytorium publikacyjne nie eksportuje
    # list student_id; ewentualna niezgodność zatrzymuje analizę.
    if n_source != n_students:
        src.close()
        raise RuntimeError(
            f"Tabela wejściowa narusza zasadę 1 uczeń = 1 rekord: "
            f"N={n_source:,}, unikalne student_id={n_students:,}."
        )

    src.close()

    # -------------------------------------------------------------------------
    # Tworzymy osobną bazę wynikową. Baza wejściowa pozostaje nietknięta.
    # -------------------------------------------------------------------------

    con = duckdb.connect(str(output_db))
    con.execute(
        f"ATTACH '{sql_path(input_db)}' AS src (READ_ONLY)"
    )

    age_status_ok = (
        f"{qident(col_age_status)} = 'wiek_przypisany'"
        if col_age_status
        else f"TRY_CAST({qident(col_age)} AS INTEGER) IS NOT NULL"
    )

    # HFZ cut-points are stored in config/hfz_cutpoints.csv.
    # They are loaded into DuckDB so the mapping used by the analysis is explicit.
    if not CUTOFFS_CSV.exists():
        raise FileNotFoundError(f"Missing HFZ cut-point file: {CUTOFFS_CSV}")

    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW hfz_cutpoints AS
        SELECT
            TRY_CAST(age AS INTEGER) AS age,
            CASE
                WHEN LOWER(TRIM(CAST(sex AS VARCHAR))) = 'girls' THEN 'dz'
                WHEN LOWER(TRIM(CAST(sex AS VARCHAR))) = 'boys' THEN 'ch'
                ELSE NULL
            END AS sex_code,
            TRY_CAST(cutpoint_shuttles AS INTEGER) AS cutpoint_shuttles
        FROM read_csv_auto('{sql_path(CUTOFFS_CSV)}', header=true)
        """
    )

    con.execute(
        f"""
        CREATE OR REPLACE TABLE uczniowie2025_hfz_nowy_wiek AS
        WITH a AS (
            SELECT
                s.*,
                cp.cutpoint_shuttles AS prog_hfz_fitnessgram
            FROM src.{qident(INPUT_TABLE)} AS s
            LEFT JOIN hfz_cutpoints AS cp
              ON TRY_CAST(s.{qident(col_age)} AS INTEGER) = cp.age
             AND CAST(s.{qident(col_sex)} AS VARCHAR) = cp.sex_code
        )
        SELECT
            *,
            CASE
                WHEN NOT ({age_status_ok})
                    THEN 'brak_jednoznacznego_wieku'
                WHEN TRY_CAST({qident(col_age)} AS INTEGER) NOT BETWEEN 10 AND 19
                    THEN 'wiek_poza_10_19'
                WHEN CAST({qident(col_sex)} AS VARCHAR) NOT IN ('dz', 'ch')
                    OR {qident(col_sex)} IS NULL
                    THEN 'nieprawidlowa_plec'
                WHEN TRY_CAST({qident(col_beep)} AS DOUBLE) IS NULL
                    THEN 'brak_20mSRT'
                WHEN prog_hfz_fitnessgram IS NULL
                    THEN 'brak_progu_hfz'
                WHEN TRY_CAST({qident(col_beep)} AS DOUBLE) >= prog_hfz_fitnessgram
                    THEN 'HFZ'
                ELSE 'ponizej_HFZ'
            END AS status_hfz_nowy,

            CASE
                WHEN ({age_status_ok})
                 AND TRY_CAST({qident(col_age)} AS INTEGER) BETWEEN 10 AND 19
                 AND CAST({qident(col_sex)} AS VARCHAR) IN ('dz', 'ch')
                 AND TRY_CAST({qident(col_beep)} AS DOUBLE) IS NOT NULL
                 AND prog_hfz_fitnessgram IS NOT NULL
                THEN
                    CASE
                        WHEN TRY_CAST({qident(col_beep)} AS DOUBLE) >= prog_hfz_fitnessgram
                        THEN 1 ELSE 0
                    END
                ELSE NULL
            END AS hfz_fitnessgram_nowy

        FROM a
        """
    )

    con.execute(
        """
        CREATE OR REPLACE VIEW uczniowie2025_hfz_final_10_19 AS
        SELECT *
        FROM uczniowie2025_hfz_nowy_wiek
        WHERE hfz_fitnessgram_nowy IS NOT NULL
        """
    )

    # Eksport analitycznej próby 10–19.
    con.execute(
        f"""
        COPY (
            SELECT *
            FROM uczniowie2025_hfz_final_10_19
        )
        TO '{sql_path(output_parquet)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )

    # -------------------------------------------------------------------------
    # PRZEPŁYW / LICZEBNOŚCI
    # -------------------------------------------------------------------------

    status_counts = con.execute(
        """
        SELECT status_hfz_nowy, COUNT(*) AS n
        FROM uczniowie2025_hfz_nowy_wiek
        GROUP BY 1
        ORDER BY 1
        """
    ).df()

    def nstatus(status: str) -> int:
        x = status_counts.loc[status_counts["status_hfz_nowy"] == status, "n"]
        return int(x.iloc[0]) if len(x) else 0

    n_no_age = nstatus("brak_jednoznacznego_wieku")
    n_out_age = nstatus("wiek_poza_10_19")
    n_bad_sex = nstatus("nieprawidlowa_plec")
    n_missing_beep = nstatus("brak_20mSRT")
    n_no_thr = nstatus("brak_progu_hfz")
    n_hfz = nstatus("HFZ")
    n_below = nstatus("ponizej_HFZ")
    n_final = n_hfz + n_below

    n_age_assigned = n_source - n_no_age
    n_age_10_19 = (
        n_source
        - n_no_age
        - n_out_age
    )
    n_valid_sex_10_19 = n_age_10_19 - n_bad_sex

    sex_final = con.execute(
        f"""
        SELECT
            {qident(col_sex)} AS plec,
            COUNT(*) AS n
        FROM uczniowie2025_hfz_final_10_19
        GROUP BY 1
        ORDER BY 1
        """
    ).df()

    n_girls = int(
        sex_final.loc[sex_final["plec"] == "dz", "n"].iloc[0]
    ) if (sex_final["plec"] == "dz").any() else 0

    n_boys = int(
        sex_final.loc[sex_final["plec"] == "ch", "n"].iloc[0]
    ) if (sex_final["plec"] == "ch").any() else 0

    flow = pd.DataFrame(
        [
            ["Wyjściowa baza", n_source],
            ["Brak jednoznacznie przypisanego wieku", n_no_age],
            ["Wiek przypisany", n_age_assigned],
            ["Poza zakresem 10–19 lat", n_out_age],
            ["Wiek 10–19 lat", n_age_10_19],
            ["Nieprawidłowa / nierozpoznana płeć w wieku 10–19", n_bad_sex],
            ["Wiek 10–19 + prawidłowa płeć", n_valid_sex_10_19],
            ["Brak wyniku 20mSRT", n_missing_beep],
            ["Dostępny 20mSRT, ale brak progu HFZ", n_no_thr],
            ["Końcowa próba HFZ", n_final],
            ["Dziewczęta w próbie HFZ", n_girls],
            ["Chłopcy w próbie HFZ", n_boys],
            ["Osiąga HFZ", n_hfz],
            ["Nie osiąga HFZ", n_below],
        ],
        columns=["etap", "n"],
    )
    flow["pct_bazy_wyjsciowej"] = (100 * flow["n"] / n_source).round(4)
    flow.to_csv(FILE_FLOW, index=False, encoding="utf-8-sig")

    # -------------------------------------------------------------------------
    # HFZ OGÓŁEM / PŁEĆ / WIEK / WIEK × PŁEĆ
    # -------------------------------------------------------------------------

    overall = con.execute(
        """
        SELECT
            COUNT(*) AS n,
            SUM(hfz_fitnessgram_nowy) AS n_hfz
        FROM uczniowie2025_hfz_final_10_19
        """
    ).df()
    overall = add_ci(overall)
    overall.to_csv(FILE_OVERALL, index=False, encoding="utf-8-sig")

    by_sex = con.execute(
        f"""
        SELECT
            {qident(col_sex)} AS plec,
            COUNT(*) AS n,
            SUM(hfz_fitnessgram_nowy) AS n_hfz
        FROM uczniowie2025_hfz_final_10_19
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    by_sex = add_ci(by_sex)
    by_sex.to_csv(FILE_SEX, index=False, encoding="utf-8-sig")

    by_age = con.execute(
        f"""
        SELECT
            TRY_CAST({qident(col_age)} AS INTEGER) AS wiek,
            COUNT(*) AS n,
            SUM(hfz_fitnessgram_nowy) AS n_hfz
        FROM uczniowie2025_hfz_final_10_19
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    by_age = add_ci(by_age)
    by_age.to_csv(FILE_AGE, index=False, encoding="utf-8-sig")

    by_age_sex = con.execute(
        f"""
        SELECT
            TRY_CAST({qident(col_age)} AS INTEGER) AS wiek,
            {qident(col_sex)} AS plec,
            COUNT(*) AS n,
            SUM(hfz_fitnessgram_nowy) AS n_hfz,
            MIN(prog_hfz_fitnessgram) AS prog_hfz
        FROM uczniowie2025_hfz_final_10_19
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).df()
    by_age_sex = add_ci(by_age_sex)
    by_age_sex.to_csv(FILE_AGE_SEX, index=False, encoding="utf-8-sig")

    # -------------------------------------------------------------------------
    # RÓŻNICE MIĘDZY KOLEJNYMI GRUPAMI WIEKU
    # -------------------------------------------------------------------------

    diffs = []
    for sex in ["dz", "ch"]:
        s = by_age_sex[by_age_sex["plec"] == sex].sort_values("wiek")
        for age in range(10, 19):
            a = s[s["wiek"] == age]
            b = s[s["wiek"] == age + 1]
            if len(a) == 1 and len(b) == 1:
                p1 = float(a.iloc[0]["hfz_pct"])
                p2 = float(b.iloc[0]["hfz_pct"])
                diffs.append(
                    {
                        "plec": sex,
                        "wiek_ml": age,
                        "wiek_st": age + 1,
                        "hfz_pct_ml": p1,
                        "hfz_pct_st": p2,
                        "roznica_pp_starszy_minus_ml": round(p2 - p1, 3),
                    }
                )

    diffs_df = pd.DataFrame(diffs)
    diffs_df.to_csv(FILE_DIFFS, index=False, encoding="utf-8-sig")

    comp_1415 = diffs_df[
        (diffs_df["wiek_ml"] == 14)
        & (diffs_df["wiek_st"] == 15)
    ].copy()
    comp_1415.to_csv(FILE_1415, index=False, encoding="utf-8-sig")

    # -------------------------------------------------------------------------
    # PORÓWNANIE STAREGO I NOWEGO HFZ — TYLKO AUDYT, JEŚLI STARY STATUS ISTNIEJE
    # -------------------------------------------------------------------------

    old_new_note = "Brak starej kolumny HFZ w bazie źródłowej."
    if col_old_hfz:
        old_new = con.execute(
            f"""
            SELECT
                TRY_CAST({qident(col_old_hfz)} AS INTEGER) AS hfz_stary,
                hfz_fitnessgram_nowy AS hfz_nowy,
                COUNT(*) AS n
            FROM uczniowie2025_hfz_nowy_wiek
            WHERE TRY_CAST({qident(col_old_hfz)} AS INTEGER) IN (0,1)
              AND hfz_fitnessgram_nowy IN (0,1)
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        ).df()
        old_new.to_csv(FILE_OLD_NEW, index=False, encoding="utf-8-sig")
        n_flip = int(
            old_new.loc[old_new["hfz_stary"] != old_new["hfz_nowy"], "n"].sum()
        )
        n_comp = int(old_new["n"].sum())
        old_new_note = (
            f"Porównano stary i nowy HFZ dla {n_comp:,} rekordów; "
            f"zmiana statusu u {n_flip:,}."
        )

    # -------------------------------------------------------------------------
    # KONTROLA
    # -------------------------------------------------------------------------

    # Kontrola rachunkowa etapów.
    flow_sum_ok = (
        n_no_age
        + n_out_age
        + n_bad_sex
        + n_missing_beep
        + n_no_thr
        + n_final
        == n_source
    )

    duplicate_ok = (n_source == n_students)

    control = [
        "PONOWNE WYLICZENIE HFZ PO NOWYM WIEKU — 2025",
        "=" * 92,
        "",
        f"Baza wejściowa: {input_db}",
        f"Baza wynikowa: {output_db}",
        f"Parquet finalnej próby: {output_parquet}",
        f"Tabela wynikowa: uczniowie2025_hfz_nowy_wiek",
        f"Widok finalny: uczniowie2025_hfz_final_10_19",
        "",
        "REGUŁA WIEKU:",
        "Pełne ukończone lata na 30.04.2025; używana jest kolumna wiek_fitnessgram.",
        "",
        "PROGI FITNESSGRAM PACER:",
        "Dziewczęta: 10=17, 11=20, 12=23, 13=25, 14=27, 15=30, 16=32, 17=35, 18–19=38.",
        "Chłopcy:    10=17, 11=20, 12=23, 13=29, 14=36, 15=42, 16=47, 17=50, 18–19=54.",
        "",
        "PRZEPŁYW:",
        flow.to_string(index=False),
        "",
        f"N wierszy źródłowych: {n_source:,}",
        f"N unikalnych student_id: {n_students:,}",
        f"Kontrola 1 uczeń = 1 rekord: {'OK' if duplicate_ok else 'UWAGA — są duplikaty'}",
        f"Kontrola sumy etapów = N źródłowe: {'OK' if flow_sum_ok else 'BŁĄD'}",
        "",
        "HFZ OGÓŁEM:",
        overall.to_string(index=False),
        "",
        "HFZ WG PŁCI:",
        by_sex.to_string(index=False),
        "",
        "HFZ 14 -> 15 LAT:",
        comp_1415.to_string(index=False) if len(comp_1415) else "Brak kompletu danych.",
        "",
        "AUDYT STARY VS NOWY HFZ:",
        old_new_note,
        "",
        "UWAGA:",
        "Pojedynczy niecałkowity wynik 20mSRT (117,01) pozostaje w bazie jako wartość źródłowa.",
        "Nie wpływa to na jego klasyfikację HFZ, ponieważ wynik jest znacznie powyżej wszystkich progów.",
    ]
    FILE_CONTROL.write_text("\n".join(control), encoding="utf-8")

    con.execute("DETACH src")
    con.close()

    print("\nGOTOWE")
    print(f"N źródłowe:       {n_source:,}")
    print(f"Bez wieku:         {n_no_age:,}")
    print(f"Poza 10–19 lat:    {n_out_age:,}")
    print(f"Wiek 10–19 lat:    {n_age_10_19:,}")
    print(f"Brak 20mSRT:       {n_missing_beep:,}")
    print(f"Końcowa próba HFZ: {n_final:,}")
    print(f"Dziewczęta:        {n_girls:,}")
    print(f"Chłopcy:           {n_boys:,}")
    print("")
    print(overall.to_string(index=False))
    print("")
    print("14 -> 15 lat:")
    print(comp_1415.to_string(index=False) if len(comp_1415) else "Brak danych.")
    print("")
    print(f"Raporty: {OUT_DIR}")
    print(f"Baza:    {output_db}")
    print(f"Parquet: {output_parquet}")
    print("=" * 100)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nBŁĄD:", exc)
        sys.exit(1)
