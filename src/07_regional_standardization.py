from __future__ import annotations

import math
import sys
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# =============================================================================
# REGIONAL DIRECT STANDARDIZATION OF HFZ — 2025
#
# Main standard population:
#   national age-by-sex distribution of all eligible students aged 10–19 years
#   before exclusion for missing 20mSRT results.
#
# The script generates:
#   - crude HFZ percentages by voivodeship,
#   - directly standardized HFZ percentages with 95% CIs,
#   - the regional table used for Figure 3,
#   - Figure 3,
#   - an Excel workbook and control report.
#
# Age = completed years on 30 April 2025.
# HFZ = hfz_fitnessgram_nowy.
# =============================================================================


ROOT = Path.cwd()
DATA_DIR = ROOT / "data" / "derived"

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

FILE_WEIGHTS = OUT_DIR / "01_standard_weights_age_sex.csv"
FILE_CELLS = OUT_DIR / "02_HFZ_cells_voivodeship_age_sex.csv"
FILE_REGIONAL = OUT_DIR / "regional_HFZ_standardized.csv"
FILE_SUMMARY = OUT_DIR / "03_regional_standardization_summary.csv"
FILE_XLSX = OUT_DIR / "regional_HFZ_standardization_2025.xlsx"
FILE_CONTROL = OUT_DIR / "regional_HFZ_standardization_2025_control.txt"

FIG3_PNG = OUT_DIR / "Figure3_HFZ_voivodeships_standardized.png"
FIG3_SVG = OUT_DIR / "Figure3_HFZ_voivodeships_standardized.svg"


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def find_latest_db() -> Path:
    candidates: list[Path] = []
    for pattern in DB_PATTERNS:
        candidates.extend(DATA_DIR.glob(pattern))

    if not candidates:
        raise FileNotFoundError(
            "Final HFZ database not found in data/derived. "
            "Expected a file matching: " + ", ".join(DB_PATTERNS)
        )

    return max(candidates, key=lambda p: p.stat().st_mtime)


def check_object(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    try:
        con.execute(f"DESCRIBE {qident(name)}").df()
        return True
    except Exception:
        return False


def get_columns(con: duckdb.DuckDBPyConnection, object_name: str) -> list[str]:
    return (
        con.execute(f"DESCRIBE {qident(object_name)}")
        .df()["column_name"]
        .astype(str)
        .tolist()
    )


def normal_ci_pct(p: float, n: int) -> tuple[float, float]:
    """95% Wald CI for an unstandardized proportion; p is on the 0–1 scale."""
    if n <= 0:
        return np.nan, np.nan
    se = math.sqrt(p * (1 - p) / n)
    lo = max(0.0, p - 1.96 * se)
    hi = min(1.0, p + 1.96 * se)
    return 100 * lo, 100 * hi


def standardized_rate_and_ci(group: pd.DataFrame) -> tuple[float, float, float]:
    """
    Direct standardization:
        P_std = sum_j w_j * p_j

    Variance:
        Var(P_std) = sum_j w_j^2 * p_j(1-p_j)/n_j

    j = 20 age-by-sex cells (10 ages × 2 sexes).
    """
    if group["n_cc"].le(0).any():
        bad = group.loc[group["n_cc"].le(0), ["age", "sex", "n_cc"]]
        raise RuntimeError(
            "At least one voivodeship × age × sex cell has n=0:\n"
            + bad.to_string(index=False)
        )

    p = group["p_hfz"].to_numpy(dtype=float)
    n = group["n_cc"].to_numpy(dtype=float)
    w = group["weight"].to_numpy(dtype=float)

    std = float(np.sum(w * p))
    var = float(np.sum((w ** 2) * p * (1 - p) / n))
    se = math.sqrt(max(var, 0.0))

    lo = max(0.0, std - 1.96 * se)
    hi = min(1.0, std + 1.96 * se)
    return 100 * std, 100 * lo, 100 * hi


def main() -> None:
    print("=" * 100)
    print("REGIONAL DIRECT STANDARDIZATION OF HFZ — 2025")
    print("Standard: national age-by-sex distribution of all eligible students")
    print("=" * 100)

    db_path = find_latest_db()
    con = duckdb.connect(str(db_path), read_only=True)

    if not check_object(con, TABLE_ALL):
        raise RuntimeError(f"Missing table: {TABLE_ALL}")
    if not check_object(con, VIEW_FINAL):
        raise RuntimeError(f"Missing view: {VIEW_FINAL}")

    cols_all = get_columns(con, TABLE_ALL)
    cols_final = get_columns(con, VIEW_FINAL)

    required = [AGE_COL, SEX_COL, VOIV_COL, HFZ_COL, "student_id"]
    missing_all = [c for c in required if c not in cols_all]
    missing_final = [c for c in required if c not in cols_final]

    if missing_all:
        raise KeyError(f"Missing columns in {TABLE_ALL}: {missing_all}")
    if missing_final:
        raise KeyError(f"Missing columns in {VIEW_FINAL}: {missing_final}")

    A = qident(AGE_COL)
    S = qident(SEX_COL)
    V = qident(VOIV_COL)
    H = qident(HFZ_COL)
    ID = qident("student_id")
    ALL = qident(TABLE_ALL)
    FINAL = qident(VIEW_FINAL)

    n_source = int(con.execute(f"SELECT COUNT(*) FROM {ALL}").fetchone()[0])
    n_unique = int(
        con.execute(f"SELECT COUNT(DISTINCT {ID}) FROM {ALL}").fetchone()[0]
    )

    eligible_where = (
        f"TRY_CAST({A} AS INTEGER) BETWEEN 10 AND 19 "
        f"AND CAST({S} AS VARCHAR) IN ('dz','ch')"
    )

    n_eligible = int(
        con.execute(
            f"SELECT COUNT(*) FROM {ALL} WHERE {eligible_where}"
        ).fetchone()[0]
    )
    n_final = int(con.execute(f"SELECT COUNT(*) FROM {FINAL}").fetchone()[0])

    if n_source != n_unique:
        raise RuntimeError(
            f"One-student-one-row check failed: N={n_source:,}, "
            f"unique student_id={n_unique:,}"
        )

    # -------------------------------------------------------------------------
    # SINGLE STANDARD POPULATION
    # National age-by-sex distribution of all eligible students before
    # exclusion for missing 20mSRT.
    # -------------------------------------------------------------------------

    weights = con.execute(
        f"""
        SELECT
            TRY_CAST({A} AS INTEGER) AS age,
            CAST({S} AS VARCHAR) AS sex,
            COUNT(*)::BIGINT AS n_standard
        FROM {ALL}
        WHERE {eligible_where}
        GROUP BY 1,2
        ORDER BY 1,2
        """
    ).df()

    weights["weight"] = weights["n_standard"] / weights["n_standard"].sum()
    weights["weight_pct"] = 100 * weights["weight"]

    if len(weights) != 20:
        raise RuntimeError(
            f"Expected 20 age-by-sex standard cells, obtained {len(weights)}."
        )
    if not np.isclose(weights["weight"].sum(), 1.0):
        raise RuntimeError("Standard weights do not sum to 1.")

    # -------------------------------------------------------------------------
    # HFZ IN 320 VOIVODESHIP × AGE × SEX CELLS
    # -------------------------------------------------------------------------

    cells = con.execute(
        f"""
        SELECT
            CAST({V} AS VARCHAR) AS voivodeship,
            TRY_CAST({A} AS INTEGER) AS age,
            CAST({S} AS VARCHAR) AS sex,
            COUNT(*)::BIGINT AS n_cc,
            SUM(TRY_CAST({H} AS INTEGER))::BIGINT AS n_hfz
        FROM {FINAL}
        GROUP BY 1,2,3
        ORDER BY 1,2,3
        """
    ).df()

    cells["voivodeship"] = cells["voivodeship"].astype(str).str.strip()
    cells["p_hfz"] = cells["n_hfz"] / cells["n_cc"]

    cells = cells.merge(
        weights[["age", "sex", "weight"]],
        on=["age", "sex"],
        how="left",
        validate="m:1",
    )

    n_voiv = cells["voivodeship"].nunique()
    n_cells = len(cells)

    if n_voiv != EXPECTED_VOIVODESHIPS:
        raise RuntimeError(
            f"Expected {EXPECTED_VOIVODESHIPS} voivodeships, obtained {n_voiv}."
        )
    if n_cells != EXPECTED_CELLS:
        raise RuntimeError(
            f"Expected {EXPECTED_CELLS} voivodeship × age × sex cells, "
            f"obtained {n_cells}."
        )
    if cells["weight"].isna().any():
        raise RuntimeError("Missing standard weights in one or more cells.")

    # -------------------------------------------------------------------------
    # CRUDE AND STANDARDIZED REGIONAL RESULTS
    # -------------------------------------------------------------------------

    rows: list[dict] = []

    for voiv, g in cells.groupby("voivodeship", sort=True):
        n = int(g["n_cc"].sum())
        n_hfz = int(g["n_hfz"].sum())
        p_raw = n_hfz / n
        raw_lo, raw_hi = normal_ci_pct(p_raw, n)

        std, std_lo, std_hi = standardized_rate_and_ci(g)

        rows.append(
            {
                "voivodeship": voiv,
                "N_complete_case": n,
                "n_HFZ": n_hfz,
                "HFZ_crude_pct": 100 * p_raw,
                "HFZ_crude_CI95_low": raw_lo,
                "HFZ_crude_CI95_high": raw_hi,
                "HFZ_standardized_pct": std,
                "HFZ_standardized_CI95_low": std_lo,
                "HFZ_standardized_CI95_high": std_hi,
                "standardized_minus_crude_pp": std - 100 * p_raw,
            }
        )

    regional = pd.DataFrame(rows)
    regional["rank_standardized"] = (
        regional["HFZ_standardized_pct"]
        .rank(ascending=False, method="min")
        .astype(int)
    )
    regional = regional.sort_values(
        "HFZ_standardized_pct", ascending=False
    ).reset_index(drop=True)

    # -------------------------------------------------------------------------
    # NATIONAL CHECK
    # -------------------------------------------------------------------------

    national_cells = (
        cells.groupby(["age", "sex"], as_index=False)
        .agg(n_cc=("n_cc", "sum"), n_hfz=("n_hfz", "sum"))
        .merge(weights[["age", "sex", "weight"]], on=["age", "sex"], how="left")
    )
    national_cells["p_hfz"] = (
        national_cells["n_hfz"] / national_cells["n_cc"]
    )

    national_raw = (
        100
        * national_cells["n_hfz"].sum()
        / national_cells["n_cc"].sum()
    )
    national_standardized = float(
        100
        * np.sum(
            national_cells["weight"] * national_cells["p_hfz"]
        )
    )

    max_row = regional.loc[regional["HFZ_standardized_pct"].idxmax()]
    min_row = regional.loc[regional["HFZ_standardized_pct"].idxmin()]
    regional_range = float(
        max_row["HFZ_standardized_pct"] - min_row["HFZ_standardized_pct"]
    )

    summary = pd.DataFrame(
        [
            ["National crude HFZ, %", national_raw],
            ["National standardized HFZ, %", national_standardized],
            ["Standardized regional range, p.p.", regional_range],
        ],
        columns=["measure", "value"],
    )

    # -------------------------------------------------------------------------
    # FIGURE 3
    # -------------------------------------------------------------------------

    plot_df = regional.sort_values(
        "HFZ_standardized_pct", ascending=True
    ).reset_index(drop=True)

    fig, ax = plt.subplots(figsize=(8.0, 6.5))
    y = np.arange(len(plot_df))
    x = plot_df["HFZ_standardized_pct"].to_numpy()

    xerr_low = (
        x - plot_df["HFZ_standardized_CI95_low"].to_numpy()
    )
    xerr_high = (
        plot_df["HFZ_standardized_CI95_high"].to_numpy() - x
    )

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
    ax.set_yticklabels(plot_df["voivodeship"])
    ax.set_xlabel("Students achieving HFZ (%)")
    ax.set_ylabel("")
    ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()

    fig.savefig(FIG3_PNG, dpi=600, bbox_inches="tight")
    fig.savefig(FIG3_SVG, bbox_inches="tight")
    plt.close(fig)

    # -------------------------------------------------------------------------
    # OUTPUTS
    # -------------------------------------------------------------------------

    weights.to_csv(FILE_WEIGHTS, index=False, encoding="utf-8-sig")
    cells.to_csv(FILE_CELLS, index=False, encoding="utf-8-sig")
    regional.to_csv(FILE_REGIONAL, index=False, encoding="utf-8-sig")
    summary.to_csv(FILE_SUMMARY, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(FILE_XLSX, engine="openpyxl") as writer:
        weights.to_excel(writer, sheet_name="Standard_weights", index=False)
        cells.to_excel(writer, sheet_name="Cells_320", index=False)
        regional.to_excel(writer, sheet_name="Regional_HFZ", index=False)
        summary.to_excel(writer, sheet_name="Summary", index=False)

    control = [
        "REGIONAL DIRECT STANDARDIZATION OF HFZ — 2025",
        "=" * 100,
        "",
        f"Database: {db_path}",
        f"All-student table: {TABLE_ALL}",
        f"Final HFZ view: {VIEW_FINAL}",
        f"Age variable: {AGE_COL} (completed years on 30 April 2025)",
        f"HFZ variable: {HFZ_COL}",
        "",
        "STANDARD POPULATION",
        "National age-by-sex distribution of all eligible students aged 10–19 years",
        "before exclusion for missing 20mSRT.",
        f"N standard population: {n_eligible:,}",
        "",
        "COUNTS",
        f"N source: {n_source:,}",
        f"N unique student_id: {n_unique:,}",
        f"N eligible: {n_eligible:,}",
        f"N final HFZ complete cases: {n_final:,}",
        f"N voivodeships: {n_voiv}",
        f"N voivodeship × age × sex cells: {n_cells}",
        "",
        "CHECKS",
        f"N source = {EXPECTED_SOURCE_N:,}: "
        f"{'OK' if n_source == EXPECTED_SOURCE_N else 'CHECK'}",
        f"N eligible = {EXPECTED_ELIGIBLE_N:,}: "
        f"{'OK' if n_eligible == EXPECTED_ELIGIBLE_N else 'CHECK'}",
        f"N final = {EXPECTED_FINAL_N:,}: "
        f"{'OK' if n_final == EXPECTED_FINAL_N else 'CHECK'}",
        f"One student = one row: "
        f"{'OK' if n_source == n_unique else 'ERROR'}",
        f"20 standard age-by-sex cells: "
        f"{'OK' if len(weights) == 20 else 'ERROR'}",
        f"320 regional cells: "
        f"{'OK' if n_cells == EXPECTED_CELLS else 'ERROR'}",
        "",
        "NATIONAL CHECK",
        f"National crude HFZ: {national_raw:.6f}%",
        f"National standardized HFZ: {national_standardized:.6f}%",
        "",
        "REGIONAL EXTREMES — STANDARDIZED VALUES",
        f"Highest: {max_row['voivodeship']} "
        f"{max_row['HFZ_standardized_pct']:.6f}%",
        f"Lowest: {min_row['voivodeship']} "
        f"{min_row['HFZ_standardized_pct']:.6f}%",
        f"Range: {regional_range:.6f} p.p.",
        "",
        "METHOD",
        "Direct standardization uses fixed national age-by-sex weights.",
        "95% CIs for standardized percentages use:",
        "Var(P_std) = sum_j w_j^2 * p_j(1-p_j)/n_j.",
        "Figure 3 is generated directly from regional_HFZ_standardized.csv.",
        "",
        "FILES",
        str(FILE_WEIGHTS),
        str(FILE_CELLS),
        str(FILE_REGIONAL),
        str(FILE_XLSX),
        str(FIG3_PNG),
        str(FIG3_SVG),
    ]

    FILE_CONTROL.write_text("\n".join(control), encoding="utf-8")

    con.close()

    print("\nDONE")
    print(f"Standard population N: {n_eligible:,}")
    print(f"Voivodeships:          {n_voiv}")
    print(f"Regional cells:        {n_cells}")
    print(f"National standardized HFZ: {national_standardized:.6f}%")
    print("")
    print(f"Regional table: {FILE_REGIONAL}")
    print(f"Figure 3:      {FIG3_PNG}")
    print(f"Control:       {FILE_CONTROL}")
    print("=" * 100)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nERROR:", exc)
        sys.exit(1)
