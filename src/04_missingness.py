from __future__ import annotations

import math
import sys
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from scipy.stats import chi2_contingency
except Exception:
    chi2_contingency = None


# =============================================================================
# ANALIZA KOMPLETNOŚCI I SELEKCJI — 20mSRT / HFZ, 2025
#
# Skrypt:
# 1) ocenia pokrycie populacji docelowej przez eksport "Sportowe Talenty",
# 2) ocenia braki 20mSRT wśród uczniów 10–19 lat z prawidłową płcią,
# 3) zestawia braki według wieku, płci, wieku × płci i województwa,
# 4) oblicza testy chi-kwadrat, V Craméra / phi i RR dziewczęta vs chłopcy,
# 5) generuje finalną Ryc. S1: braki 20mSRT według wieku i płci,
# 6) zapisuje komplet tabel do Excela i CSV.
#
# WAŻNE:
# - wiek musi pochodzić z finalnej kolumny wiek_fitnessgram
#   (pełne ukończone lata na 30.04.2025);
# - do analizy braków NIE wymagamy dostępnego Beep — właśnie brak Beep jest wynikiem;
# - nie używamy starej kolumny wieku ani daty rejestracji.
# =============================================================================


# =============================================================================
# USTAWIENIA
# =============================================================================

ROOT = Path.cwd()
BAZA_DIR = ROOT / "data" / "derived"

# Skrypt najpierw szuka finalnej bazy HFZ, a następnie bazy z finalnym wiekiem.
DB_PATTERNS = [
    "sportowe_talenty_2025_HFZ_po_nowym_wieku*.duckdb",
    "sportowe_talenty_2025_wiek_fitnessgram*.duckdb",
]

# Preferowane tabele zawierające WSZYSTKICH uczniów z eksportu, także bez 20mSRT.
TABLE_CANDIDATES = [
    "uczniowie2025_hfz_nowy_wiek",
    "uczniowie2025_wiek_fitnessgram",
]

# Oficjalny mianownik — rok szkolny 2024/2025.
# Wartości ustalone na podstawie przekazanych zestawień administracyjnych:
# - klasy IV–VIII szkół podstawowych:                  1 966 119
# - objęte obowiązkiem szkoły ponadpodstawowe:        1 660 336
# - szkoły artystyczne realizujące kształcenie ogólne:   21 003
PRIMARY_GRADES_IV_VIII = 1_966_119
UPPER_SECONDARY = 1_660_336
ART_GENERAL_EDUCATION = 21_003
TARGET_POPULATION = PRIMARY_GRADES_IV_VIII + UPPER_SECONDARY + ART_GENERAL_EDUCATION

OUT_DIR = ROOT / "results" / "04_missingness"
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUT_XLSX = OUT_DIR / "kompletnosc_i_selekcja_20mSRT_2025.xlsx"
OUT_TXT = OUT_DIR / "kompletnosc_i_selekcja_20mSRT_2025_kontrola.txt"

OUT_COVERAGE = OUT_DIR / "01_pokrycie_populacji_docelowej.csv"
OUT_OVERALL = OUT_DIR / "02_braki_20mSRT_ogolem.csv"
OUT_AGE = OUT_DIR / "03_braki_20mSRT_wiek.csv"
OUT_SEX = OUT_DIR / "04_braki_20mSRT_plec.csv"
OUT_AGE_SEX = OUT_DIR / "05_braki_20mSRT_wiek_plec.csv"
OUT_VOIV = OUT_DIR / "06_braki_20mSRT_wojewodztwo.csv"
OUT_TESTS = OUT_DIR / "07_testy_brakow_20mSRT.csv"
OUT_FLOW = OUT_DIR / "08_przeplyw_kwalifikacji_do_analizy_brakow.csv"

FIG_S1_PNG = OUT_DIR / "Figure_S1_missing_20mSRT_by_age_and_sex.png"
FIG_S1_SVG = OUT_DIR / "Figure_S1_missing_20mSRT_by_age_and_sex.svg"


# =============================================================================
# FUNKCJE POMOCNICZE
# =============================================================================

def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def find_latest_db() -> Path:
    candidates = []
    for pattern in DB_PATTERNS:
        candidates.extend(BAZA_DIR.glob(pattern))

    if not candidates:
        raise FileNotFoundError(
            "Nie znaleziono finalnej bazy 2025 w folderze Baza.\n"
            "Szukano:\n  - " + "\n  - ".join(DB_PATTERNS)
        )

    # Preferuj finalną bazę HFZ; w obrębie typu wybierz najnowszą.
    def rank(p: Path):
        is_hfz = "HFZ_po_nowym_wieku" in p.name
        return (1 if is_hfz else 0, p.stat().st_mtime)

    return max(candidates, key=rank)


def get_columns(con, table: str) -> list[str]:
    return (
        con.execute(f"DESCRIBE {qident(table)}")
        .df()["column_name"]
        .astype(str)
        .tolist()
    )


def choose_table(con) -> str:
    all_objects = con.execute("SHOW TABLES").df()["name"].astype(str).tolist()

    for candidate in TABLE_CANDIDATES:
        if candidate in all_objects:
            return candidate

    # Próba bezpośredniego DESCRIBE, gdy obiekt jest widokiem.
    for candidate in TABLE_CANDIDATES:
        try:
            con.execute(f"DESCRIBE {qident(candidate)}").df()
            return candidate
        except Exception:
            pass

    raise RuntimeError(
        "Nie znaleziono tabeli zawierającej wszystkich uczniów.\n"
        f"Szukano: {TABLE_CANDIDATES}\n"
        f"Dostępne obiekty: {all_objects}"
    )


def choose_column(columns: list[str], candidates: list[str], required=True):
    lookup = {c.lower(): c for c in columns}
    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]

    if required:
        raise KeyError(
            "Nie znaleziono wymaganej kolumny.\n"
            f"Szukano: {candidates}\n"
            f"Dostępne kolumny: {columns}"
        )
    return None


def add_rates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["n_ma_20mSRT"] = out["n"] - out["n_brak_20mSRT"]
    out["pct_brak_20mSRT"] = (100 * out["n_brak_20mSRT"] / out["n"]).round(3)
    out["pct_ma_20mSRT"] = (100 * out["n_ma_20mSRT"] / out["n"]).round(3)
    return out


def cramers_v_from_table(contingency: np.ndarray):
    if chi2_contingency is None:
        return None, None, None

    chi2, p, dof, _ = chi2_contingency(contingency, correction=False)
    n = contingency.sum()
    r, k = contingency.shape
    denom = min(r - 1, k - 1)
    v = math.sqrt(chi2 / (n * denom)) if denom > 0 and n > 0 else float("nan")
    return chi2, p, v


def rr_and_ci(a_missing, a_total, b_missing, b_total):
    """
    RR = ryzyko braku 20mSRT u grupy A / ryzyko w grupie B.
    W analizie A = dziewczęta, B = chłopcy.
    """
    p_a = a_missing / a_total
    p_b = b_missing / b_total
    rr = p_a / p_b

    # SE(log RR) dla dwóch niezależnych proporcji.
    se_log_rr = math.sqrt(
        (1 / a_missing) - (1 / a_total)
        + (1 / b_missing) - (1 / b_total)
    )
    low = math.exp(math.log(rr) - 1.96 * se_log_rr)
    high = math.exp(math.log(rr) + 1.96 * se_log_rr)
    return rr, low, high


def p_text(p):
    if p is None or pd.isna(p):
        return "nie obliczono"
    return "<0,001" if p < 0.001 else f"{p:.4f}"


# =============================================================================
# GŁÓWNA ANALIZA
# =============================================================================

def main():
    print("=" * 96)
    print("ANALIZA KOMPLETNOŚCI I SELEKCJI — 20mSRT / HFZ, 2025")
    print("=" * 96)

    if TARGET_POPULATION != 3_647_458:
        raise RuntimeError("Błąd kontrolny: populacja docelowa nie sumuje się do 3 647 458.")

    db_path = find_latest_db()
    con = duckdb.connect(str(db_path), read_only=True)

    table = choose_table(con)
    cols = get_columns(con, table)

    col_id = choose_column(cols, ["student_id"])
    col_age = choose_column(cols, ["wiek_fitnessgram"])
    col_sex = choose_column(cols, ["plec", "płeć", "sex"])
    col_beep = choose_column(cols, ["beep", "pacer", "20msrt"])

    col_voiv = choose_column(
        cols,
        ["wojewodztwo", "województwo", "woj"],
        required=False,
    )

    T = qident(table)
    ID = qident(col_id)
    AGE = qident(col_age)
    SEX = qident(col_sex)
    BEEP = qident(col_beep)

    # ---------------------------------------------------------
    # Kontrola 1 uczeń = 1 rekord
    # ---------------------------------------------------------
    n_source = int(con.execute(f"SELECT COUNT(*) FROM {T}").fetchone()[0])
    n_unique = int(con.execute(f"SELECT COUNT(DISTINCT {ID}) FROM {T}").fetchone()[0])

    if n_source != n_unique:
        raise RuntimeError(
            f"Tabela nie spełnia zasady 1 uczeń = 1 rekord: "
            f"N={n_source:,}, unikalne student_id={n_unique:,}."
        )

    # ---------------------------------------------------------
    # Pokrycie populacji docelowej
    # ---------------------------------------------------------
    n_not_represented = TARGET_POPULATION - n_source
    coverage_pct = 100 * n_source / TARGET_POPULATION
    gap_pct = 100 * n_not_represented / TARGET_POPULATION

    coverage = pd.DataFrame([
        {
            "pozycja": "Szkoła podstawowa, klasy IV–VIII",
            "n": PRIMARY_GRADES_IV_VIII,
            "pct_populacji_docelowej": 100 * PRIMARY_GRADES_IV_VIII / TARGET_POPULATION,
        },
        {
            "pozycja": "Szkoły ponadpodstawowe objęte obowiązkiem testowania",
            "n": UPPER_SECONDARY,
            "pct_populacji_docelowej": 100 * UPPER_SECONDARY / TARGET_POPULATION,
        },
        {
            "pozycja": "Szkoły artystyczne realizujące kształcenie ogólne",
            "n": ART_GENERAL_EDUCATION,
            "pct_populacji_docelowej": 100 * ART_GENERAL_EDUCATION / TARGET_POPULATION,
        },
        {
            "pozycja": "POPULACJA DOCELOWA",
            "n": TARGET_POPULATION,
            "pct_populacji_docelowej": 100.0,
        },
        {
            "pozycja": "Uczniowie reprezentowani w eksporcie",
            "n": n_source,
            "pct_populacji_docelowej": coverage_pct,
        },
        {
            "pozycja": "Uczniowie niereprezentowani w eksporcie",
            "n": n_not_represented,
            "pct_populacji_docelowej": gap_pct,
        },
    ])
    coverage["pct_populacji_docelowej"] = coverage["pct_populacji_docelowej"].round(3)

    # ---------------------------------------------------------
    # Populacja do analizy braków:
    # finalny wiek 10–19 + prawidłowa płeć, niezależnie od Beep
    # ---------------------------------------------------------
    eligible_where = (
        f"TRY_CAST({AGE} AS INTEGER) BETWEEN 10 AND 19 "
        f"AND CAST({SEX} AS VARCHAR) IN ('dz','ch')"
    )

    n_no_age = int(con.execute(
        f"SELECT COUNT(*) FROM {T} WHERE TRY_CAST({AGE} AS INTEGER) IS NULL"
    ).fetchone()[0])

    n_out_age = int(con.execute(
        f"""
        SELECT COUNT(*)
        FROM {T}
        WHERE TRY_CAST({AGE} AS INTEGER) IS NOT NULL
          AND TRY_CAST({AGE} AS INTEGER) NOT BETWEEN 10 AND 19
        """
    ).fetchone()[0])

    n_invalid_sex_ageeligible = int(con.execute(
        f"""
        SELECT COUNT(*)
        FROM {T}
        WHERE TRY_CAST({AGE} AS INTEGER) BETWEEN 10 AND 19
          AND (
              {SEX} IS NULL
              OR CAST({SEX} AS VARCHAR) NOT IN ('dz','ch')
          )
        """
    ).fetchone()[0])

    overall = con.execute(
        f"""
        SELECT
            COUNT(*)::BIGINT AS n,
            SUM(CASE WHEN TRY_CAST({BEEP} AS DOUBLE) IS NULL THEN 1 ELSE 0 END)::BIGINT
                AS n_brak_20mSRT
        FROM {T}
        WHERE {eligible_where}
        """
    ).df()
    overall = add_rates(overall)

    n_eligible = int(overall.loc[0, "n"])
    n_missing = int(overall.loc[0, "n_brak_20mSRT"])
    n_available = int(overall.loc[0, "n_ma_20mSRT"])
    pct_missing = float(overall.loc[0, "pct_brak_20mSRT"])

    # ---------------------------------------------------------
    # Wiek
    # ---------------------------------------------------------
    by_age = con.execute(
        f"""
        SELECT
            TRY_CAST({AGE} AS INTEGER) AS wiek,
            COUNT(*)::BIGINT AS n,
            SUM(CASE WHEN TRY_CAST({BEEP} AS DOUBLE) IS NULL THEN 1 ELSE 0 END)::BIGINT
                AS n_brak_20mSRT
        FROM {T}
        WHERE {eligible_where}
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    by_age = add_rates(by_age)

    # ---------------------------------------------------------
    # Płeć
    # ---------------------------------------------------------
    by_sex = con.execute(
        f"""
        SELECT
            CAST({SEX} AS VARCHAR) AS plec,
            COUNT(*)::BIGINT AS n,
            SUM(CASE WHEN TRY_CAST({BEEP} AS DOUBLE) IS NULL THEN 1 ELSE 0 END)::BIGINT
                AS n_brak_20mSRT
        FROM {T}
        WHERE {eligible_where}
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    by_sex = add_rates(by_sex)
    by_sex["plec_label"] = by_sex["plec"].map({"dz": "Dziewczęta", "ch": "Chłopcy"})

    # ---------------------------------------------------------
    # Wiek × płeć
    # ---------------------------------------------------------
    by_age_sex = con.execute(
        f"""
        SELECT
            TRY_CAST({AGE} AS INTEGER) AS wiek,
            CAST({SEX} AS VARCHAR) AS plec,
            COUNT(*)::BIGINT AS n,
            SUM(CASE WHEN TRY_CAST({BEEP} AS DOUBLE) IS NULL THEN 1 ELSE 0 END)::BIGINT
                AS n_brak_20mSRT
        FROM {T}
        WHERE {eligible_where}
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).df()
    by_age_sex = add_rates(by_age_sex)
    by_age_sex["plec_label"] = by_age_sex["plec"].map(
        {"dz": "Girls", "ch": "Boys"}
    )

    # ---------------------------------------------------------
    # Województwo
    # ---------------------------------------------------------
    if col_voiv:
        VOIV = qident(col_voiv)
        by_voiv = con.execute(
            f"""
            SELECT
                CAST({VOIV} AS VARCHAR) AS wojewodztwo,
                COUNT(*)::BIGINT AS n,
                SUM(CASE WHEN TRY_CAST({BEEP} AS DOUBLE) IS NULL THEN 1 ELSE 0 END)::BIGINT
                    AS n_brak_20mSRT
            FROM {T}
            WHERE {eligible_where}
            GROUP BY 1
            ORDER BY 1
            """
        ).df()
        by_voiv = add_rates(by_voiv)
    else:
        by_voiv = pd.DataFrame(
            columns=["wojewodztwo", "n", "n_brak_20mSRT",
                     "n_ma_20mSRT", "pct_brak_20mSRT", "pct_ma_20mSRT"]
        )

    # ---------------------------------------------------------
    # Testy braków
    # ---------------------------------------------------------
    tests = []

    # wiek
    age_ct = by_age[["n_brak_20mSRT", "n_ma_20mSRT"]].to_numpy()
    chi2_age, p_age, v_age = cramers_v_from_table(age_ct)
    tests.append({
        "czynnik": "wiek",
        "chi2": chi2_age,
        "df": (len(by_age) - 1) if chi2_age is not None else None,
        "p": p_age,
        "miara_efektu": "V Craméra",
        "efekt": v_age,
    })

    # płeć
    sex_ct = by_sex[["n_brak_20mSRT", "n_ma_20mSRT"]].to_numpy()
    chi2_sex, p_sex, v_sex = cramers_v_from_table(sex_ct)
    tests.append({
        "czynnik": "płeć",
        "chi2": chi2_sex,
        "df": 1 if chi2_sex is not None else None,
        "p": p_sex,
        "miara_efektu": "phi",
        "efekt": v_sex,
    })

    # województwo
    if not by_voiv.empty:
        voiv_ct = by_voiv[["n_brak_20mSRT", "n_ma_20mSRT"]].to_numpy()
        chi2_voiv, p_voiv, v_voiv = cramers_v_from_table(voiv_ct)
        tests.append({
            "czynnik": "województwo",
            "chi2": chi2_voiv,
            "df": (len(by_voiv) - 1) if chi2_voiv is not None else None,
            "p": p_voiv,
            "miara_efektu": "V Craméra",
            "efekt": v_voiv,
        })

    tests_df = pd.DataFrame(tests)
    for c in ["chi2", "p", "efekt"]:
        if c in tests_df.columns:
            tests_df[c] = pd.to_numeric(tests_df[c], errors="coerce")

    # RR dziewczęta vs chłopcy
    girls = by_sex.loc[by_sex["plec"] == "dz"].iloc[0]
    boys = by_sex.loc[by_sex["plec"] == "ch"].iloc[0]

    rr, rr_low, rr_high = rr_and_ci(
        int(girls["n_brak_20mSRT"]), int(girls["n"]),
        int(boys["n_brak_20mSRT"]), int(boys["n"]),
    )

    rr_df = pd.DataFrame([{
        "porownanie": "Dziewczęta vs chłopcy",
        "RR_braku_20mSRT": rr,
        "CI95_low": rr_low,
        "CI95_high": rr_high,
        "ryzyko_dziewczeta_pct": girls["pct_brak_20mSRT"],
        "ryzyko_chlopcy_pct": boys["pct_brak_20mSRT"],
        "roznica_pp_dz_minus_ch": (
            float(girls["pct_brak_20mSRT"]) - float(boys["pct_brak_20mSRT"])
        ),
    }])

    # ---------------------------------------------------------
    # Przepływ
    # ---------------------------------------------------------
    flow = pd.DataFrame([
        ["Populacja docelowa objęta obowiązkiem testowania", TARGET_POPULATION],
        ["Reprezentowani w źródłowej bazie 2025", n_source],
        ["Niereprezentowani w źródłowej bazie", n_not_represented],
        ["Brak jednoznacznego wieku", n_no_age],
        ["Wiek poza 10–19 lat", n_out_age],
        ["Nieprawidłowa / nierozpoznana płeć w wieku 10–19", n_invalid_sex_ageeligible],
        ["Wiek 10–19 + prawidłowa płeć (mianownik analizy braków)", n_eligible],
        ["Brak wyniku 20mSRT", n_missing],
        ["Dostępny wynik 20mSRT", n_available],
    ], columns=["etap", "n"])

    # ---------------------------------------------------------
    # RYCINA S1 — braki według wieku i płci
    # ---------------------------------------------------------
    fig, ax = plt.subplots(figsize=(7.5, 5.0))

    for sex, label, marker in [
        ("dz", "Girls", "o"),
        ("ch", "Boys", "s"),
    ]:
        s = by_age_sex[by_age_sex["plec"] == sex].sort_values("wiek")
        ax.plot(
            s["wiek"],
            s["pct_brak_20mSRT"],
            marker=marker,
            linewidth=1.8,
            markersize=5.5,
            label=label,
        )

    ax.axhline(
        pct_missing,
        linestyle="--",
        linewidth=1.2,
        label=f"Overall ({pct_missing:.1f}%)",
    )

    ax.set_xlabel("Age (years)")
    ax.set_ylabel("Missing 20mSRT result (%)")
    ax.set_xticks(range(10, 20))
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()

    fig.savefig(FIG_S1_PNG, dpi=600, bbox_inches="tight")
    fig.savefig(FIG_S1_SVG, bbox_inches="tight")
    plt.close(fig)

    # ---------------------------------------------------------
    # Zapis CSV
    # ---------------------------------------------------------
    coverage.to_csv(OUT_COVERAGE, index=False, encoding="utf-8-sig")
    overall.to_csv(OUT_OVERALL, index=False, encoding="utf-8-sig")
    by_age.to_csv(OUT_AGE, index=False, encoding="utf-8-sig")
    by_sex.to_csv(OUT_SEX, index=False, encoding="utf-8-sig")
    by_age_sex.to_csv(OUT_AGE_SEX, index=False, encoding="utf-8-sig")
    by_voiv.to_csv(OUT_VOIV, index=False, encoding="utf-8-sig")
    tests_df.to_csv(OUT_TESTS, index=False, encoding="utf-8-sig")
    flow.to_csv(OUT_FLOW, index=False, encoding="utf-8-sig")

    # ---------------------------------------------------------
    # Zapis Excel
    # ---------------------------------------------------------
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        coverage.to_excel(writer, sheet_name="Pokrycie_populacji", index=False)
        flow.to_excel(writer, sheet_name="Przeplyw", index=False)
        overall.to_excel(writer, sheet_name="Braki_ogolem", index=False)
        by_age.to_excel(writer, sheet_name="Braki_wiek", index=False)
        by_sex.to_excel(writer, sheet_name="Braki_plec", index=False)
        by_age_sex.to_excel(writer, sheet_name="Braki_wiek_plec", index=False)
        by_voiv.to_excel(writer, sheet_name="Braki_wojewodztwo", index=False)
        tests_df.to_excel(writer, sheet_name="Testy", index=False)
        rr_df.to_excel(writer, sheet_name="RR_plec", index=False)

    # ---------------------------------------------------------
    # Raport kontrolny
    # ---------------------------------------------------------
    lines = [
        "ANALIZA KOMPLETNOŚCI I SELEKCJI — 20mSRT / HFZ, 2025",
        "=" * 92,
        "",
        f"Baza: {db_path}",
        f"Tabela: {table}",
        f"Kolumna wieku: {col_age}",
        f"Kolumna płci: {col_sex}",
        f"Kolumna 20mSRT: {col_beep}",
        f"Kolumna województwa: {col_voiv if col_voiv else 'BRAK'}",
        "",
        "POZIOM 1 — POKRYCIE POPULACJI DOCELOWEJ",
        f"Populacja docelowa: {TARGET_POPULATION:,}",
        f"Reprezentowani w eksporcie: {n_source:,} ({coverage_pct:.3f}%)",
        f"Niereprezentowani w eksporcie: {n_not_represented:,} ({gap_pct:.3f}%)",
        "",
        "POZIOM 2 — BRAK 20mSRT WŚRÓD UCZNIÓW 10–19 LAT Z PRAWIDŁOWĄ PŁCIĄ",
        f"Mianownik: {n_eligible:,}",
        f"Brak 20mSRT: {n_missing:,} ({pct_missing:.3f}%)",
        f"Dostępny 20mSRT: {n_available:,} ({100-pct_missing:.3f}%)",
        "",
        "PŁEĆ",
        by_sex.to_string(index=False),
        "",
        f"RR braku 20mSRT, dziewczęta vs chłopcy: "
        f"{rr:.4f} (95% CI {rr_low:.4f}–{rr_high:.4f})",
        "",
        "TESTY",
    ]

    for _, r in tests_df.iterrows():
        lines.append(
            f"{r['czynnik']}: chi2={r['chi2']:.3f}, df={int(r['df']) if pd.notna(r['df']) else 'NA'}, "
            f"p={p_text(r['p'])}, {r['miara_efektu']}={r['efekt']:.4f}"
            if pd.notna(r["chi2"]) and pd.notna(r["efekt"])
            else f"{r['czynnik']}: test nieobliczony"
        )

    lines.extend([
        "",
        "KONTROLE",
        f"N źródłowe = N unikalnych student_id: {'OK' if n_source == n_unique else 'BŁĄD'}",
        f"Populacja docelowa = 3 647 458: {'OK' if TARGET_POPULATION == 3_647_458 else 'BŁĄD'}",
        f"Mianownik analizy braków = {n_eligible:,}",
        f"Brak + dostępny 20mSRT = mianownik: "
        f"{'OK' if n_missing + n_available == n_eligible else 'BŁĄD'}",
        "",
        "RYCINA",
        str(FIG_S1_PNG),
        str(FIG_S1_SVG),
    ])

    OUT_TXT.write_text("\n".join(lines), encoding="utf-8")

    con.close()

    print("\nGOTOWE")
    print(f"Populacja docelowa: {TARGET_POPULATION:,}")
    print(f"Pokrycie eksportu:   {n_source:,} / {TARGET_POPULATION:,} = {coverage_pct:.2f}%")
    print(f"Brak 20mSRT:         {n_missing:,} / {n_eligible:,} = {pct_missing:.2f}%")
    print(f"Excel:                {OUT_XLSX}")
    print(f"Rycina S1 PNG:        {FIG_S1_PNG}")
    print(f"Rycina S1 SVG:        {FIG_S1_SVG}")
    print(f"Raport kontrolny:     {OUT_TXT}")
    print("=" * 96)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nBŁĄD:", exc)
        sys.exit(1)
