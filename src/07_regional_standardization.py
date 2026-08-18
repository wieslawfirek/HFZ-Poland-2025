from __future__ import annotations

import math
import sys
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# =============================================================================
# STANDARYZACJA REGIONALNA HFZ — 2025
#
# Cel:
#   ponownie policzyć wyniki wojewódzkie po korekcie wieku oraz porównać
#   dwa warianty populacji standardowej:
#
#   A. struktura wieku × płci KOŃCOWEJ próby HFZ (complete cases);
#   B. struktura wieku × płci wszystkich uczniów kwalifikujących się
#      PRZED wyłączeniem z powodu braku 20mSRT.
#
# Wariant B odpowiada analizie wrażliwości zaleconej w ocenie metodologicznej.
#
# Skrypt generuje:
#   - surowe odsetki HFZ wg województwa,
#   - standaryzację A,
#   - standaryzację B,
#   - porównanie A vs B,
#   - 95% CI,
#   - ranking i rozstęp wartości,
#   - Tabelę S6 w wariancie B,
#   - Figure 3 z dokładnie tej samej tabeli,
#   - dodatkową rycinę porównującą A i B,
#   - plik kontrolny.
#
# WAŻNE:
#   - wiek = wiek_fitnessgram, czyli pełne lata na 30.04.2025;
#   - HFZ = hfz_fitnessgram_nowy;
#   - wyniki regionalne są liczone odrębnie od modelu wiek × płeć.
# =============================================================================


# =============================================================================
# USTAWIENIA
# =============================================================================

ROOT = Path.cwd()
BAZA_DIR = ROOT / "data" / "derived"

DB_PATTERNS = [
    "sportowe_talenty_2025_HFZ_po_nowym_wieku*.duckdb",
]

TABLE_ALL = "uczniowie2025_hfz_nowy_wiek"
VIEW_FINAL = "uczniowie2025_hfz_final_10_19"

AGE_COL = "wiek_fitnessgram"
SEX_COL = "plec"
VOIV_COL = "wojewodztwo"
HFZ_COL = "hfz_fitnessgram_nowy"

EXPECTED_SOURCE_N = 3_000_210
EXPECTED_ELIGIBLE_N = 2_969_503
EXPECTED_FINAL_N = 2_715_127
EXPECTED_VOIVODESHIPS = 16
EXPECTED_CELLS = 16 * 10 * 2

OUT_DIR = ROOT / "results" / "07_regional_standardization"
OUT_DIR.mkdir(parents=True, exist_ok=True)

FILE_WEIGHTS = OUT_DIR / "01_wagi_standardowe_A_B_wiek_plec.csv"
FILE_CELLS = OUT_DIR / "02_HFZ_komorki_woj_wiek_plec.csv"
FILE_RESULTS = OUT_DIR / "03_wojewodztwa_surowe_i_standaryzowane_A_B.csv"
FILE_REGIONAL_TABLE = OUT_DIR / "regional_HFZ_standard_B.csv"
FILE_SUMMARY = OUT_DIR / "04_podsumowanie_porownania_standardow.csv"
FILE_XLSX = OUT_DIR / "standaryzacja_regionalna_HFZ_2025.xlsx"
FILE_CONTROL = OUT_DIR / "standaryzacja_regionalna_HFZ_2025_kontrola.txt"

FIG3_PNG = OUT_DIR / "Figure3_HFZ_wojewodztwa_standard_B.png"
FIG3_SVG = OUT_DIR / "Figure3_HFZ_wojewodztwa_standard_B.svg"

FIG_SENS_PNG = OUT_DIR / "Figure_Sensitivity_standard_A_vs_B.png"
FIG_SENS_SVG = OUT_DIR / "Figure_Sensitivity_standard_A_vs_B.svg"


# =============================================================================
# FUNKCJE
# =============================================================================

def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def find_latest_db() -> Path:
    candidates = []
    for pattern in DB_PATTERNS:
        candidates.extend(BAZA_DIR.glob(pattern))

    if not candidates:
        raise FileNotFoundError(
            "Nie znaleziono finalnej bazy HFZ w folderze Baza.\n"
            "Szukano: " + ", ".join(DB_PATTERNS)
        )

    return max(candidates, key=lambda p: p.stat().st_mtime)


def check_object(con, name: str):
    try:
        con.execute(f"DESCRIBE {qident(name)}").df()
        return True
    except Exception:
        return False


def get_columns(con, object_name: str) -> list[str]:
    return (
        con.execute(f"DESCRIBE {qident(object_name)}")
        .df()["column_name"]
        .astype(str)
        .tolist()
    )


def normal_ci_pct(p: float, n: int):
    """
    95% Wald CI dla zwykłej proporcji.
    Przy obecnych bardzo dużych liczebnościach różnice względem Wilsona są pomijalne.
    p jest proporcją 0–1.
    """
    if n <= 0:
        return np.nan, np.nan
    se = math.sqrt(p * (1 - p) / n)
    lo = max(0.0, p - 1.96 * se)
    hi = min(1.0, p + 1.96 * se)
    return 100 * lo, 100 * hi


def std_rate_and_ci(group: pd.DataFrame, weight_col: str):
    """
    Bezpośrednia standaryzacja:
        P_std = sum_j w_j * p_j

    Wariancja:
        Var(P_std) = sum_j w_j^2 * p_j(1-p_j)/n_j

    Komórki j = 10 grup wieku × 2 płcie.
    """
    if group["n_cc"].le(0).any():
        bad = group.loc[group["n_cc"].le(0), ["wiek", "plec", "n_cc"]]
        raise RuntimeError(
            "Co najmniej jedna komórka województwo × wiek × płeć ma n=0:\n"
            + bad.to_string(index=False)
        )

    p = group["p_hfz"].to_numpy(dtype=float)
    n = group["n_cc"].to_numpy(dtype=float)
    w = group[weight_col].to_numpy(dtype=float)

    std = float(np.sum(w * p))
    var = float(np.sum((w ** 2) * p * (1 - p) / n))
    se = math.sqrt(max(var, 0.0))

    lo = max(0.0, std - 1.96 * se)
    hi = min(1.0, std + 1.96 * se)
    return 100 * std, 100 * lo, 100 * hi


def clean_voiv_name(x):
    if pd.isna(x):
        return x
    s = str(x).strip()
    return s


# =============================================================================
# ANALIZA
# =============================================================================

def main():
    print("=" * 100)
    print("STANDARYZACJA REGIONALNA HFZ — 2025")
    print("A = standard końcowej próby; B = standard przed wyłączeniem braków 20mSRT")
    print("=" * 100)

    db_path = find_latest_db()
    con = duckdb.connect(str(db_path), read_only=True)

    if not check_object(con, TABLE_ALL):
        raise RuntimeError(f"Brak tabeli {TABLE_ALL}.")
    if not check_object(con, VIEW_FINAL):
        raise RuntimeError(f"Brak widoku {VIEW_FINAL}.")

    cols_all = get_columns(con, TABLE_ALL)
    cols_final = get_columns(con, VIEW_FINAL)

    required_all = [AGE_COL, SEX_COL, VOIV_COL, HFZ_COL, "student_id"]
    missing_all = [c for c in required_all if c not in cols_all]
    if missing_all:
        raise KeyError(
            f"W tabeli {TABLE_ALL} brakuje kolumn: {missing_all}\n"
            f"Dostępne: {cols_all}"
        )

    required_final = [AGE_COL, SEX_COL, VOIV_COL, HFZ_COL, "student_id"]
    missing_final = [c for c in required_final if c not in cols_final]
    if missing_final:
        raise KeyError(
            f"W widoku {VIEW_FINAL} brakuje kolumn: {missing_final}\n"
            f"Dostępne: {cols_final}"
        )

    A = qident(AGE_COL)
    S = qident(SEX_COL)
    V = qident(VOIV_COL)
    H = qident(HFZ_COL)
    ID = qident("student_id")
    ALL = qident(TABLE_ALL)
    FINAL = qident(VIEW_FINAL)

    # -------------------------------------------------------------------------
    # KONTROLE LICZEBNOŚCI
    # -------------------------------------------------------------------------

    n_source = int(con.execute(f"SELECT COUNT(*) FROM {ALL}").fetchone()[0])
    n_unique = int(con.execute(f"SELECT COUNT(DISTINCT {ID}) FROM {ALL}").fetchone()[0])

    eligible_where = (
        f"TRY_CAST({A} AS INTEGER) BETWEEN 10 AND 19 "
        f"AND CAST({S} AS VARCHAR) IN ('dz','ch')"
    )

    n_eligible = int(
        con.execute(f"SELECT COUNT(*) FROM {ALL} WHERE {eligible_where}").fetchone()[0]
    )
    n_final = int(con.execute(f"SELECT COUNT(*) FROM {FINAL}").fetchone()[0])

    if n_source != n_unique:
        raise RuntimeError(
            f"Nie spełniono 1 uczeń = 1 rekord: N={n_source:,}, unique={n_unique:,}"
        )

    # -------------------------------------------------------------------------
    # STANDARD A — struktura wieku × płci końcowej próby HFZ
    # -------------------------------------------------------------------------

    weights_A = con.execute(
        f"""
        SELECT
            TRY_CAST({A} AS INTEGER) AS wiek,
            CAST({S} AS VARCHAR) AS plec,
            COUNT(*)::BIGINT AS n_standard_A
        FROM {FINAL}
        GROUP BY 1,2
        ORDER BY 1,2
        """
    ).df()

    weights_A["w_A"] = weights_A["n_standard_A"] / weights_A["n_standard_A"].sum()

    # -------------------------------------------------------------------------
    # STANDARD B — struktura wieku × płci przed wyłączeniem braków 20mSRT
    # -------------------------------------------------------------------------

    weights_B = con.execute(
        f"""
        SELECT
            TRY_CAST({A} AS INTEGER) AS wiek,
            CAST({S} AS VARCHAR) AS plec,
            COUNT(*)::BIGINT AS n_standard_B
        FROM {ALL}
        WHERE {eligible_where}
        GROUP BY 1,2
        ORDER BY 1,2
        """
    ).df()

    weights_B["w_B"] = weights_B["n_standard_B"] / weights_B["n_standard_B"].sum()

    weights = weights_A.merge(weights_B, on=["wiek", "plec"], how="outer", validate="1:1")
    weights["w_A_pct"] = 100 * weights["w_A"]
    weights["w_B_pct"] = 100 * weights["w_B"]
    weights["B_minus_A_pp"] = weights["w_B_pct"] - weights["w_A_pct"]

    if len(weights) != 20:
        raise RuntimeError(f"Oczekiwano 20 komórek wieku × płci, otrzymano {len(weights)}.")
    if not np.isclose(weights["w_A"].sum(), 1.0):
        raise RuntimeError("Wagi A nie sumują się do 1.")
    if not np.isclose(weights["w_B"].sum(), 1.0):
        raise RuntimeError("Wagi B nie sumują się do 1.")

    # -------------------------------------------------------------------------
    # HFZ W 320 KOMÓRKACH województwo × wiek × płeć
    # -------------------------------------------------------------------------

    cells = con.execute(
        f"""
        SELECT
            CAST({V} AS VARCHAR) AS wojewodztwo,
            TRY_CAST({A} AS INTEGER) AS wiek,
            CAST({S} AS VARCHAR) AS plec,
            COUNT(*)::BIGINT AS n_cc,
            SUM(TRY_CAST({H} AS INTEGER))::BIGINT AS n_hfz
        FROM {FINAL}
        GROUP BY 1,2,3
        ORDER BY 1,2,3
        """
    ).df()

    cells["wojewodztwo"] = cells["wojewodztwo"].map(clean_voiv_name)
    cells["p_hfz"] = cells["n_hfz"] / cells["n_cc"]

    cells = cells.merge(
        weights[["wiek", "plec", "w_A", "w_B"]],
        on=["wiek", "plec"],
        how="left",
        validate="m:1",
    )

    n_voiv = cells["wojewodztwo"].nunique()
    n_cells = len(cells)

    if n_voiv != EXPECTED_VOIVODESHIPS:
        raise RuntimeError(
            f"Oczekiwano {EXPECTED_VOIVODESHIPS} województw, otrzymano {n_voiv}."
        )
    if n_cells != EXPECTED_CELLS:
        raise RuntimeError(
            f"Oczekiwano {EXPECTED_CELLS} komórek woj×wiek×płeć, otrzymano {n_cells}."
        )
    if cells[["w_A", "w_B"]].isna().any().any():
        raise RuntimeError("Brak przypisanych wag A lub B w części komórek.")

    # -------------------------------------------------------------------------
    # SUROWE I STANDARYZOWANE WYNIKI WG WOJEWÓDZTWA
    # -------------------------------------------------------------------------

    results = []

    for voiv, g in cells.groupby("wojewodztwo", sort=True):
        n = int(g["n_cc"].sum())
        n_hfz = int(g["n_hfz"].sum())
        p_raw = n_hfz / n
        raw_lo, raw_hi = normal_ci_pct(p_raw, n)

        std_A, A_lo, A_hi = std_rate_and_ci(g, "w_A")
        std_B, B_lo, B_hi = std_rate_and_ci(g, "w_B")

        results.append({
            "wojewodztwo": voiv,
            "N_complete_case": n,
            "n_HFZ": n_hfz,
            "HFZ_surowy_pct": 100 * p_raw,
            "HFZ_surowy_CI95_low": raw_lo,
            "HFZ_surowy_CI95_high": raw_hi,
            "HFZ_std_A_pct": std_A,
            "HFZ_std_A_CI95_low": A_lo,
            "HFZ_std_A_CI95_high": A_hi,
            "HFZ_std_B_pct": std_B,
            "HFZ_std_B_CI95_low": B_lo,
            "HFZ_std_B_CI95_high": B_hi,
            "B_minus_A_pp": std_B - std_A,
            "B_minus_raw_pp": std_B - 100 * p_raw,
        })

    res = pd.DataFrame(results)

    # Rank 1 = najwyższy HFZ
    res["rank_raw"] = res["HFZ_surowy_pct"].rank(ascending=False, method="min").astype(int)
    res["rank_A"] = res["HFZ_std_A_pct"].rank(ascending=False, method="min").astype(int)
    res["rank_B"] = res["HFZ_std_B_pct"].rank(ascending=False, method="min").astype(int)
    res["rank_B_minus_A"] = res["rank_B"] - res["rank_A"]

    # Posortuj finalną tabelę wg wariantu B.
    res = res.sort_values("HFZ_std_B_pct", ascending=False).reset_index(drop=True)

    # -------------------------------------------------------------------------
    # KONTROLA STANDARDYZACJI NA POZIOMIE KRAJOWYM
    # -------------------------------------------------------------------------

    national_cells = (
        cells.groupby(["wiek", "plec"], as_index=False)
        .agg(n_cc=("n_cc", "sum"), n_hfz=("n_hfz", "sum"))
        .merge(weights[["wiek", "plec", "w_A", "w_B"]], on=["wiek", "plec"], how="left")
    )
    national_cells["p_hfz"] = national_cells["n_hfz"] / national_cells["n_cc"]

    national_raw = 100 * national_cells["n_hfz"].sum() / national_cells["n_cc"].sum()
    national_std_A = 100 * np.sum(national_cells["w_A"] * national_cells["p_hfz"])
    national_std_B = 100 * np.sum(national_cells["w_B"] * national_cells["p_hfz"])

    # Standard A powinien odtworzyć surowy wynik krajowy.
    national_A_check = abs(national_raw - national_std_A)

    # -------------------------------------------------------------------------
    # PODSUMOWANIE PORÓWNANIA A VS B
    # -------------------------------------------------------------------------

    def extremes(col):
        maxrow = res.loc[res[col].idxmax()]
        minrow = res.loc[res[col].idxmin()]
        return (
            maxrow["wojewodztwo"], float(maxrow[col]),
            minrow["wojewodztwo"], float(minrow[col]),
            float(maxrow[col] - minrow[col]),
        )

    raw_max_v, raw_max, raw_min_v, raw_min, raw_range = extremes("HFZ_surowy_pct")
    A_max_v, A_max, A_min_v, A_min, A_range = extremes("HFZ_std_A_pct")
    B_max_v, B_max, B_min_v, B_min, B_range = extremes("HFZ_std_B_pct")

    max_abs_diff = float(res["B_minus_A_pp"].abs().max())
    mean_abs_diff = float(res["B_minus_A_pp"].abs().mean())
    max_rank_change = int(res["rank_B_minus_A"].abs().max())
    rank_corr = float(res[["rank_A", "rank_B"]].corr(method="spearman").iloc[0, 1])

    summary = pd.DataFrame([
        ["National raw HFZ, %", national_raw],
        ["National standardized A HFZ, %", national_std_A],
        ["National standardized B HFZ, %", national_std_B],
        ["|National raw - standardized A|, p.p.", national_A_check],
        ["Max |B-A| across voivodeships, p.p.", max_abs_diff],
        ["Mean |B-A| across voivodeships, p.p.", mean_abs_diff],
        ["Max absolute rank change A vs B", max_rank_change],
        ["Spearman rank correlation A vs B", rank_corr],
        ["Raw regional range, p.p.", raw_range],
        ["Standard A regional range, p.p.", A_range],
        ["Standard B regional range, p.p.", B_range],
    ], columns=["miara", "wartosc"])

    # -------------------------------------------------------------------------
    # TABELA S6 — wariant B
    # -------------------------------------------------------------------------

    s6 = res[[
        "wojewodztwo",
        "N_complete_case",
        "HFZ_surowy_pct",
        "HFZ_surowy_CI95_low",
        "HFZ_surowy_CI95_high",
        "HFZ_std_B_pct",
        "HFZ_std_B_CI95_low",
        "HFZ_std_B_CI95_high",
        "B_minus_raw_pp",
    ]].copy()

    s6 = s6.rename(columns={
        "N_complete_case": "N",
        "HFZ_std_B_pct": "HFZ_standaryzowany_B_pct",
        "HFZ_std_B_CI95_low": "HFZ_standaryzowany_B_CI95_low",
        "HFZ_std_B_CI95_high": "HFZ_standaryzowany_B_CI95_high",
        "B_minus_raw_pp": "standaryzowany_minus_surowy_pp",
    })

    # -------------------------------------------------------------------------
    # FIGURE 3 — ZAWSZE Z TEJ SAMEJ TABELI S6
    # -------------------------------------------------------------------------

    plot_df = s6.sort_values("HFZ_standaryzowany_B_pct", ascending=True).reset_index(drop=True)

    fig, ax = plt.subplots(figsize=(8.0, 6.5))
    y = np.arange(len(plot_df))
    x = plot_df["HFZ_standaryzowany_B_pct"].to_numpy()
    xerr_low = x - plot_df["HFZ_standaryzowany_B_CI95_low"].to_numpy()
    xerr_high = plot_df["HFZ_standaryzowany_B_CI95_high"].to_numpy() - x

    ax.errorbar(
        x,
        y,
        xerr=np.vstack([xerr_low, xerr_high]),
        fmt="o",
        capsize=2.5,
        linewidth=1.0,
        markersize=4.5,
    )
    ax.set_yticks(y)
    ax.set_yticklabels(plot_df["wojewodztwo"])
    ax.set_xlabel("Students achieving HFZ (%)")
    ax.set_ylabel("")
    ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()
    fig.savefig(FIG3_PNG, dpi=600, bbox_inches="tight")
    fig.savefig(FIG3_SVG, bbox_inches="tight")
    plt.close(fig)

    # -------------------------------------------------------------------------
    # RYCINA SENSITIVITY — B minus A
    # -------------------------------------------------------------------------

    diff_df = res.sort_values("B_minus_A_pp", ascending=True).reset_index(drop=True)

    fig, ax = plt.subplots(figsize=(8.0, 6.5))
    y = np.arange(len(diff_df))
    ax.barh(y, diff_df["B_minus_A_pp"])
    ax.axvline(0, linewidth=1.0)
    ax.set_yticks(y)
    ax.set_yticklabels(diff_df["wojewodztwo"])
    ax.set_xlabel("Difference: standard B − standard A (percentage points)")
    ax.set_ylabel("")
    ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()
    fig.savefig(FIG_SENS_PNG, dpi=600, bbox_inches="tight")
    fig.savefig(FIG_SENS_SVG, bbox_inches="tight")
    plt.close(fig)

    # -------------------------------------------------------------------------
    # ZAPIS
    # -------------------------------------------------------------------------

    weights.to_csv(FILE_WEIGHTS, index=False, encoding="utf-8-sig")
    cells.to_csv(FILE_CELLS, index=False, encoding="utf-8-sig")
    res.to_csv(FILE_RESULTS, index=False, encoding="utf-8-sig")
    s6.to_csv(FILE_REGIONAL_TABLE, index=False, encoding="utf-8-sig")
    summary.to_csv(FILE_SUMMARY, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(FILE_XLSX, engine="openpyxl") as writer:
        weights.to_excel(writer, sheet_name="Wagi_A_B", index=False)
        cells.to_excel(writer, sheet_name="Komorki_320", index=False)
        res.to_excel(writer, sheet_name="Wyniki_A_B", index=False)
        s6.to_excel(writer, sheet_name="Regional_standard_B", index=False)
        summary.to_excel(writer, sheet_name="Podsumowanie", index=False)

    # -------------------------------------------------------------------------
    # RAPORT KONTROLNY
    # -------------------------------------------------------------------------

    control = [
        "STANDARYZACJA REGIONALNA HFZ — 2025",
        "=" * 100,
        "",
        f"Baza: {db_path}",
        f"Tabela wszystkich uczniów: {TABLE_ALL}",
        f"Widok finalnej próby HFZ: {VIEW_FINAL}",
        f"Wiek: {AGE_COL} (pełne lata na 30.04.2025)",
        f"HFZ: {HFZ_COL}",
        "",
        "LICZEBNOŚCI",
        f"N źródłowe: {n_source:,}",
        f"N unikalnych student_id: {n_unique:,}",
        f"N kwalifikowanych przed wyłączeniem braków 20mSRT (standard B): {n_eligible:,}",
        f"N końcowej próby HFZ / complete cases (standard A): {n_final:,}",
        f"N województw: {n_voiv}",
        f"N komórek województwo × wiek × płeć: {n_cells}",
        "",
        "KONTROLE WZGLĘDEM OCZEKIWANYCH LICZEBNOŚCI",
        f"N źródłowe = {EXPECTED_SOURCE_N:,}: {'OK' if n_source == EXPECTED_SOURCE_N else 'UWAGA'}",
        f"N eligible = {EXPECTED_ELIGIBLE_N:,}: {'OK' if n_eligible == EXPECTED_ELIGIBLE_N else 'UWAGA'}",
        f"N final = {EXPECTED_FINAL_N:,}: {'OK' if n_final == EXPECTED_FINAL_N else 'UWAGA'}",
        f"1 uczeń = 1 rekord: {'OK' if n_source == n_unique else 'BŁĄD'}",
        f"20 komórek wag A: {'OK' if len(weights_A) == 20 else 'BŁĄD'}",
        f"20 komórek wag B: {'OK' if len(weights_B) == 20 else 'BŁĄD'}",
        f"320 komórek regionalnych: {'OK' if n_cells == EXPECTED_CELLS else 'BŁĄD'}",
        "",
        "KONTROLA KRAJOWA",
        f"HFZ surowy krajowy: {national_raw:.6f}%",
        f"HFZ standaryzowany do standardu A: {national_std_A:.6f}%",
        f"Różnica raw - A: {national_raw - national_std_A:.9f} p.p.",
        f"HFZ standaryzowany do standardu B: {national_std_B:.6f}%",
        "",
        "PORÓWNANIE STANDARDÓW A VS B",
        f"Maksymalna |B-A| w województwach: {max_abs_diff:.6f} p.p.",
        f"Średnia |B-A| w województwach: {mean_abs_diff:.6f} p.p.",
        f"Maksymalna zmiana pozycji w rankingu: {max_rank_change}",
        f"Korelacja rang Spearmana A vs B: {rank_corr:.6f}",
        "",
        "ROZSTĘPY REGIONALNE — ZAWSZE MIĘDZY SKRAJNYMI WARTOŚCIAMI W DANYM WARIANCIE",
        f"Surowy: {raw_max_v} {raw_max:.6f}% vs {raw_min_v} {raw_min:.6f}% -> {raw_range:.6f} p.p.",
        f"Standard A: {A_max_v} {A_max:.6f}% vs {A_min_v} {A_min:.6f}% -> {A_range:.6f} p.p.",
        f"Standard B: {B_max_v} {B_max:.6f}% vs {B_min_v} {B_min:.6f}% -> {B_range:.6f} p.p.",
        "",
        "UWAGA METODOLOGICZNA",
        "Standard A = ogólnopolska struktura wieku × płci w końcowej próbie z dostępnym 20mSRT.",
        "Standard B = ogólnopolska struktura wieku × płci wszystkich kwalifikujących się uczniów",
        "             przed wyłączeniem z powodu braku 20mSRT.",
        "Figure 3 is generated directly from regional_HFZ_standard_B.csv.",
        "",
        "PLIKI",
        str(FILE_RESULTS),
        str(FILE_REGIONAL_TABLE),
        str(FILE_XLSX),
        str(FIG3_PNG),
        str(FIG3_SVG),
        str(FIG_SENS_PNG),
        str(FIG_SENS_SVG),
    ]

    FILE_CONTROL.write_text("\n".join(control), encoding="utf-8")

    con.close()

    print("\nGOTOWE")
    print(f"Standard A N: {n_final:,}")
    print(f"Standard B N: {n_eligible:,}")
    print(f"Max |B-A|:   {max_abs_diff:.4f} p.p.")
    print(f"Spearman:    {rank_corr:.4f}")
    print("")
    print(f"Tabela S6:   {FILE_REGIONAL_TABLE}")
    print(f"Figure 3:    {FIG3_PNG}")
    print(f"Porównanie:  {FIG_SENS_PNG}")
    print(f"Kontrola:    {FILE_CONTROL}")
    print("=" * 100)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nBŁĄD:", exc)
        sys.exit(1)
