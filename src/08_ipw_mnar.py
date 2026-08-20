from __future__ import annotations

import math
import sys
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf


# =============================================================================
# IPW + CELL CHECKS + POSITIVITY + MNAR — 20mSRT / HFZ, 2025
#
# The analysis performs:
# 1) model for the probability that a 20mSRT result is observed:
#       R ~ age + sex + voivodeship + age×sex
# 2) stabilizowane IPW = P(R=1) / P(R=1|X)
# 3) ograniczenie wag do 1. i 99. percentyla
# 4) alternative weights directly in 320 cells:
#       voivodeship × age × sex
# 5) positivity diagnostics
# 6) comparison:
#       - crude complete-case vs IPW
#       - STANDARYZOWANY HFZ complete-case vs STANDARYZOWANY HFZ po IPW
#         using the common national age × sex standard of all eligible students
# 7) scenariusze MNAR:
#       students with missing 20mSRT are assumed to have HFZ probability lower by
#       5, 10, or 20 percentage points than observed students
#       in the same voivodeship × age × sex cell.
#
# NOTE:
# With an IPW model based only on voivodeship, age, and sex, the weight is
# constant within each of the 320 cells. Therefore, after direct standardization
# to the same 20 age × sex cells, IPW does not change
# the within-cell HFZ proportion. The script calculates and verifies this explicitly.
# This is not an error; it follows from the estimator construction.
# =============================================================================


# =============================================================================
# SETTINGS
# =============================================================================

ROOT = Path.cwd()
BAZA_DIR = ROOT / "data" / "derived"

DB_PATTERNS = [
    "sportowe_talenty_2025_HFZ_po_nowym_wieku*.duckdb",
]

TABLE_ALL = "uczniowie2025_hfz_nowy_wiek"
VIEW_FINAL = "uczniowie2025_hfz_final_10_19"

ID_COL = "student_id"
AGE_COL = "wiek_fitnessgram"
SEX_COL = "plec"
VOIV_COL = "wojewodztwo"
HFZ_COL = "hfz_fitnessgram_nowy"

EXPECTED_SOURCE_N = 3_000_210
EXPECTED_ELIGIBLE_N = 2_969_503
EXPECTED_FINAL_N = 2_715_127
EXPECTED_CELLS = 320
EXPECTED_VOIV = 16

MNAR_DELTAS_PP = [0, 5, 10, 20]

OUT_DIR = ROOT / "results" / "08_ipw_mnar"
OUT_DIR.mkdir(parents=True, exist_ok=True)

F_CELLS = OUT_DIR / "01_komorki_320_IPW_positivity.csv"
F_OVERALL = OUT_DIR / "02_IPW_ogolem.csv"
F_AGESEX = OUT_DIR / "03_IPW_wiek_plec.csv"
F_REGIONAL = OUT_DIR / "04_IPW_wojewodztwa_standaryzowane.csv"
F_POSITIVITY = OUT_DIR / "05_positivity.csv"
F_MNAR_NAT = OUT_DIR / "06_MNAR_kraj.csv"
F_MNAR_REG = OUT_DIR / "07_MNAR_wojewodztwa.csv"
F_MODEL = OUT_DIR / "08_model_propensity_wspolczynniki.csv"
F_XLSX = OUT_DIR / "analysis_IPW_MNAR_2025.xlsx"
F_CONTROL = OUT_DIR / "analysis_IPW_MNAR_2025_kontrola.txt"

FIG_MNAR_PNG = OUT_DIR / "Figure_MNAR_scenarios_national.png"
FIG_MNAR_SVG = OUT_DIR / "Figure_MNAR_scenarios_national.svg"


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
            "Not found finalnej bazy HFZ w folderze Database.\n"
            "Szukano: " + ", ".join(DB_PATTERNS)
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


def check_object(con, name: str) -> bool:
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


def exact_repeated_quantile(values, counts, q: float) -> float:
    """
    Odtwarza liniowy kwantyl (jak np.quantile method='linear') dla tablicy,
    where each value occurs 'counts' times, without expanding millions of rows.
    """
    values = np.asarray(values, dtype=float)
    counts = np.asarray(counts, dtype=np.int64)

    mask = counts > 0
    values = values[mask]
    counts = counts[mask]

    order = np.argsort(values)
    values = values[order]
    counts = counts[order]

    cum = np.cumsum(counts)
    n = int(cum[-1])
    if n <= 0:
        return np.nan
    if n == 1:
        return float(values[0])

    h = (n - 1) * q
    k0 = int(math.floor(h))
    k1 = int(math.ceil(h))
    frac = h - k0

    def order_stat(k):
        idx = int(np.searchsorted(cum, k + 1, side="left"))
        return float(values[idx])

    v0 = order_stat(k0)
    v1 = order_stat(k1)
    return (1 - frac) * v0 + frac * v1


def truncate_weight_column(df: pd.DataFrame, raw_col: str, count_col: str, prefix: str):
    q01 = exact_repeated_quantile(df[raw_col], df[count_col], 0.01)
    q99 = exact_repeated_quantile(df[raw_col], df[count_col], 0.99)

    trunc_col = f"{prefix}_trunc"
    df[trunc_col] = df[raw_col].clip(lower=q01, upper=q99)

    affected_n = int(
        df.loc[
            (df[raw_col] < q01) | (df[raw_col] > q99),
            count_col
        ].sum()
    )
    total_n = int(df[count_col].sum())
    affected_pct = 100 * affected_n / total_n

    return q01, q99, affected_n, affected_pct, trunc_col


def effective_n(weights, counts):
    weights = np.asarray(weights, dtype=float)
    counts = np.asarray(counts, dtype=float)
    sw = np.sum(counts * weights)
    sw2 = np.sum(counts * (weights ** 2))
    return float((sw ** 2) / sw2)


def weighted_hfz(df, weight_col):
    num = float(np.sum(df["n_hfz"] * df[weight_col]))
    den = float(np.sum(df["n_observed"] * df[weight_col]))
    return 100 * num / den


def raw_hfz(df):
    return 100 * float(df["n_hfz"].sum()) / float(df["n_observed"].sum())


def std_rate_and_ci(group: pd.DataFrame, rate_col: str, std_weight_col: str = "std_weight"):
    p = group[rate_col].to_numpy(dtype=float)
    n = group["n_observed"].to_numpy(dtype=float)
    w = group[std_weight_col].to_numpy(dtype=float)

    est = float(np.sum(w * p))

    # Because IPW weights are constant within a voivodeship × age × sex cell,
    # the effective within-cell sample size remains n_observed.
    var = float(np.sum((w ** 2) * p * (1 - p) / n))
    se = math.sqrt(max(var, 0.0))
    lo = max(0.0, est - 1.96 * se)
    hi = min(1.0, est + 1.96 * se)

    return 100 * est, 100 * lo, 100 * hi


def spearman_ranks(a, b):
    return float(pd.Series(a).corr(pd.Series(b), method="spearman"))


# =============================================================================
# ANALYSIS
# =============================================================================

def main():
    print("=" * 104)
    print("IPW + CELL CHECKS + POSITIVITY + MNAR — 20mSRT / HFZ, 2025")
    print("=" * 104)

    db_path = find_latest_db()
    con = duckdb.connect(str(db_path), read_only=True)

    if not check_object(con, TABLE_ALL):
        raise RuntimeError(f"Brak tabeli {TABLE_ALL}")
    if not check_object(con, VIEW_FINAL):
        raise RuntimeError(f"Brak widoku {VIEW_FINAL}")

    cols = get_columns(con, TABLE_ALL)
    required = [ID_COL, AGE_COL, SEX_COL, VOIV_COL, HFZ_COL]
    missing_cols = [c for c in required if c not in cols]
    if missing_cols:
        raise KeyError(
            f"Missing required columns: {missing_cols}\nAvailable: {cols}"
        )

    T = qident(TABLE_ALL)
    ID = qident(ID_COL)
    AGE = qident(AGE_COL)
    SEX = qident(SEX_COL)
    VOIV = qident(VOIV_COL)
    HFZ = qident(HFZ_COL)

    eligible_where = (
        f"TRY_CAST({AGE} AS INTEGER) BETWEEN 10 AND 19 "
        f"AND CAST({SEX} AS VARCHAR) IN ('dz','ch')"
    )

    # -------------------------------------------------------------------------
    # COUNTS AND CHECKS
    # -------------------------------------------------------------------------

    n_source = int(con.execute(f"SELECT COUNT(*) FROM {T}").fetchone()[0])
    n_unique = int(con.execute(f"SELECT COUNT(DISTINCT {ID}) FROM {T}").fetchone()[0])

    n_eligible = int(
        con.execute(f"SELECT COUNT(*) FROM {T} WHERE {eligible_where}").fetchone()[0]
    )

    n_final = int(
        con.execute(
            f"SELECT COUNT(*) FROM {T} "
            f"WHERE {eligible_where} AND TRY_CAST({HFZ} AS INTEGER) IN (0,1)"
        ).fetchone()[0]
    )

    n_missing = n_eligible - n_final
    p_observed = n_final / n_eligible

    n_missing_voiv = int(
        con.execute(
            f"""
            SELECT COUNT(*)
            FROM {T}
            WHERE {eligible_where}
              AND ({VOIV} IS NULL OR TRIM(CAST({VOIV} AS VARCHAR)) = '')
            """
        ).fetchone()[0]
    )

    if n_source != n_unique:
        raise RuntimeError(
            f"one student != one row: N={n_source:,}, unique={n_unique:,}"
        )
    if n_missing_voiv > 0:
        raise RuntimeError(
            f"{n_missing_voiv:,} eligible students have no voivodeship. "
            "The current IPW model cannot be estimated correctly."
        )

    # -------------------------------------------------------------------------
    # 320 CELLS: voivodeship × age × sex
    # -------------------------------------------------------------------------

    cells = con.execute(
        f"""
        SELECT
            CAST({VOIV} AS VARCHAR) AS wojewodztwo,
            TRY_CAST({AGE} AS INTEGER) AS wiek,
            CAST({SEX} AS VARCHAR) AS plec,
            COUNT(*)::BIGINT AS n_eligible,
            SUM(CASE WHEN TRY_CAST({HFZ} AS INTEGER) IN (0,1)
                     THEN 1 ELSE 0 END)::BIGINT AS n_observed,
            SUM(CASE WHEN TRY_CAST({HFZ} AS INTEGER) IN (0,1)
                     THEN 0 ELSE 1 END)::BIGINT AS n_missing,
            SUM(CASE WHEN TRY_CAST({HFZ} AS INTEGER) = 1
                     THEN 1 ELSE 0 END)::BIGINT AS n_hfz
        FROM {T}
        WHERE {eligible_where}
        GROUP BY 1,2,3
        ORDER BY 1,2,3
        """
    ).df()

    cells["response_rate"] = cells["n_observed"] / cells["n_eligible"]
    cells["missing_rate"] = cells["n_missing"] / cells["n_eligible"]
    cells["hfz_cc"] = cells["n_hfz"] / cells["n_observed"]

    n_voiv = cells["wojewodztwo"].nunique()
    if len(cells) != EXPECTED_CELLS:
        raise RuntimeError(
            f"Expected {EXPECTED_CELLS} cells, obtained {len(cells)}."
        )
    if n_voiv != EXPECTED_VOIV:
        raise RuntimeError(
            f"Expected {EXPECTED_VOIV} voivodeships, obtained {n_voiv}."
        )
    if (cells["n_observed"] <= 0).any():
        raise RuntimeError("Positivity violation: at least one cell has zero observed 20mSRT results.")

    # -------------------------------------------------------------------------
    # STANDARD: national age × sex distribution of all eligible students before exclusion for missing 20mSRT
    # -------------------------------------------------------------------------

    standard_weights = (
        cells.groupby(["wiek", "plec"], as_index=False)
        .agg(n_standard=("n_eligible", "sum"))
    )
    standard_weights["std_weight"] = standard_weights["n_standard"] / standard_weights["n_standard"].sum()

    cells = cells.merge(
        standard_weights[["wiek", "plec", "std_weight"]],
        on=["wiek", "plec"],
        how="left",
        validate="m:1",
    )

    # -------------------------------------------------------------------------
    # MODEL LOGISTYCZNY PROPENSITY
    # R ~ age + sex + voivodeship + age×sex
    # -------------------------------------------------------------------------

    model_df = cells.copy()
    model_df["R_rate"] = model_df["response_rate"]

    propensity_model = smf.glm(
        formula="R_rate ~ C(wiek) + C(plec) + C(wojewodztwo) + C(wiek):C(plec)",
        data=model_df,
        family=sm.families.Binomial(),
        freq_weights=model_df["n_eligible"],
    ).fit()

    cells["p_logit"] = propensity_model.predict(model_df)

    if (cells["p_logit"] <= 0).any():
        raise RuntimeError("The propensity model produced p <= 0.")

    # Stabilized weight = marginal P(R=1) / conditional P(R=1|X)
    cells["w_logit_raw"] = p_observed / cells["p_logit"]

    # -------------------------------------------------------------------------
    # ALTERNATIVE CELL WEIGHTS
    # -------------------------------------------------------------------------

    cells["p_cell"] = cells["response_rate"]
    cells["w_cell_raw"] = p_observed / cells["p_cell"]

    # -------------------------------------------------------------------------
    # TRUNCATION / WINSORIZATION AT THE 1ST–99TH PERCENTILES
    # Percentiles are calculated over observed individuals, not over 320 unique weights
    # -------------------------------------------------------------------------

    (
        log_q01, log_q99, log_n_clip, log_pct_clip, log_trunc_col
    ) = truncate_weight_column(
        cells, "w_logit_raw", "n_observed", "w_logit"
    )

    (
        cell_q01, cell_q99, cell_n_clip, cell_pct_clip, cell_trunc_col
    ) = truncate_weight_column(
        cells, "w_cell_raw", "n_observed", "w_cell"
    )

    # -------------------------------------------------------------------------
    # WEIGHT DIAGNOSTICS
    # -------------------------------------------------------------------------

    neff_logit_raw = effective_n(cells["w_logit_raw"], cells["n_observed"])
    neff_logit_trunc = effective_n(cells[log_trunc_col], cells["n_observed"])

    neff_cell_raw = effective_n(cells["w_cell_raw"], cells["n_observed"])
    neff_cell_trunc = effective_n(cells[cell_trunc_col], cells["n_observed"])

    # -------------------------------------------------------------------------
    # OVERALL RESULT
    # -------------------------------------------------------------------------

    cc_overall = raw_hfz(cells)
    logit_raw_overall = weighted_hfz(cells, "w_logit_raw")
    logit_tr_overall = weighted_hfz(cells, log_trunc_col)
    cell_raw_overall = weighted_hfz(cells, "w_cell_raw")
    cell_tr_overall = weighted_hfz(cells, cell_trunc_col)

    overall = pd.DataFrame([
        {
            "analysis": "Complete-case",
            "HFZ_pct": cc_overall,
            "delta_vs_CC_pp": 0.0,
            "N_lub_Neff": n_final,
            "w_min": np.nan,
            "w_max": np.nan,
            "q01": np.nan,
            "q99": np.nan,
            "pct_obserwacji_ograniczonych": 0.0,
        },
        {
            "analysis": "Logistic IPW — raw weights",
            "HFZ_pct": logit_raw_overall,
            "delta_vs_CC_pp": logit_raw_overall - cc_overall,
            "N_lub_Neff": neff_logit_raw,
            "w_min": cells["w_logit_raw"].min(),
            "w_max": cells["w_logit_raw"].max(),
            "q01": log_q01,
            "q99": log_q99,
            "pct_obserwacji_ograniczonych": 0.0,
        },
        {
            "analysis": "Logistic IPW — 1st–99th percentile",
            "HFZ_pct": logit_tr_overall,
            "delta_vs_CC_pp": logit_tr_overall - cc_overall,
            "N_lub_Neff": neff_logit_trunc,
            "w_min": cells[log_trunc_col].min(),
            "w_max": cells[log_trunc_col].max(),
            "q01": log_q01,
            "q99": log_q99,
            "pct_obserwacji_ograniczonych": log_pct_clip,
        },
        {
            "analysis": "Cell IPW — raw weights",
            "HFZ_pct": cell_raw_overall,
            "delta_vs_CC_pp": cell_raw_overall - cc_overall,
            "N_lub_Neff": neff_cell_raw,
            "w_min": cells["w_cell_raw"].min(),
            "w_max": cells["w_cell_raw"].max(),
            "q01": cell_q01,
            "q99": cell_q99,
            "pct_obserwacji_ograniczonych": 0.0,
        },
        {
            "analysis": "Cell IPW — 1st–99th percentile",
            "HFZ_pct": cell_tr_overall,
            "delta_vs_CC_pp": cell_tr_overall - cc_overall,
            "N_lub_Neff": neff_cell_trunc,
            "w_min": cells[cell_trunc_col].min(),
            "w_max": cells[cell_trunc_col].max(),
            "q01": cell_q01,
            "q99": cell_q99,
            "pct_obserwacji_ograniczonych": cell_pct_clip,
        },
    ])

    # -------------------------------------------------------------------------
    # AGE × SEX — CC, logistic IPW, and cell IPW
    # -------------------------------------------------------------------------

    age_sex_rows = []
    for (age, sex), g in cells.groupby(["wiek", "plec"], sort=True):
        cc = raw_hfz(g)
        lg = weighted_hfz(g, log_trunc_col)
        cw = weighted_hfz(g, cell_trunc_col)

        age_sex_rows.append({
            "wiek": age,
            "plec": sex,
            "CC_pct": cc,
            "IPW_logit_pct": lg,
            "delta_logit_pp": lg - cc,
            "IPW_cell_pct": cw,
            "delta_cell_pp": cw - cc,
        })

    age_sex = pd.DataFrame(age_sex_rows)

    # -------------------------------------------------------------------------
    # VOIVODESHIPS — CRUDE AND MAIN STANDARDIZED ESTIMAND
    # -------------------------------------------------------------------------

    regional_rows = []

    for voiv, g in cells.groupby("wojewodztwo", sort=True):
        # Crude estimates may change after IPW
        raw_cc = raw_hfz(g)
        raw_logit = weighted_hfz(g, log_trunc_col)
        raw_cell = weighted_hfz(g, cell_trunc_col)

        # Within a voivodeship × age × sex cell, the weight is constant, so weighted cell HFZ
        # is mathematically identical to complete-case cell HFZ.
        g = g.copy()

        g["hfz_logit_cell"] = (
            g["n_hfz"] * g[log_trunc_col]
        ) / (
            g["n_observed"] * g[log_trunc_col]
        )

        g["hfz_cellipw_cell"] = (
            g["n_hfz"] * g[cell_trunc_col]
        ) / (
            g["n_observed"] * g[cell_trunc_col]
        )

        std_cc, ci_lo, ci_hi = std_rate_and_ci(g, "hfz_cc")
        std_logit, _, _ = std_rate_and_ci(g, "hfz_logit_cell")
        std_cell, _, _ = std_rate_and_ci(g, "hfz_cellipw_cell")

        regional_rows.append({
            "wojewodztwo": voiv,
            "N_complete_case": int(g["n_observed"].sum()),
            "raw_CC_pct": raw_cc,
            "raw_IPW_logit_pct": raw_logit,
            "raw_delta_logit_pp": raw_logit - raw_cc,
            "raw_IPW_cell_pct": raw_cell,
            "raw_delta_cell_pp": raw_cell - raw_cc,
            "standardized_CC_pct": std_cc,
            "standardized_CC_CI95_low": ci_lo,
            "standardized_CC_CI95_high": ci_hi,
            "standardized_IPW_logit_pct": std_logit,
            "std_delta_logit_pp": std_logit - std_cc,
            "standardized_IPW_cell_pct": std_cell,
            "std_delta_cell_pp": std_cell - std_cc,
        })

    regional = pd.DataFrame(regional_rows)

    regional["rank_std_CC"] = (
        regional["standardized_CC_pct"].rank(ascending=False, method="min").astype(int)
    )
    regional["rank_std_IPW_logit"] = (
        regional["standardized_IPW_logit_pct"].rank(ascending=False, method="min").astype(int)
    )
    regional["rank_std_IPW_cell"] = (
        regional["standardized_IPW_cell_pct"].rank(ascending=False, method="min").astype(int)
    )

    regional = regional.sort_values("standardized_CC_pct", ascending=False).reset_index(drop=True)

    max_std_diff_logit = float(regional["std_delta_logit_pp"].abs().max())
    max_std_diff_cell = float(regional["std_delta_cell_pp"].abs().max())

    # -------------------------------------------------------------------------
    # POSITIVITY
    # -------------------------------------------------------------------------

    positivity = pd.DataFrame([
        ["Number of cells", len(cells)],
        ["Minimum eligible n per cell", int(cells["n_eligible"].min())],
        ["Minimum observed n per cell", int(cells["n_observed"].min())],
        ["Maximum eligible n per cell", int(cells["n_eligible"].max())],
        ["Minimalny rzeczywisty response rate", float(cells["response_rate"].min())],
        ["Maksymalny rzeczywisty response rate", float(cells["response_rate"].max())],
        ["Minimalne p z modelu logistycznego", float(cells["p_logit"].min())],
        ["Maksymalne p z modelu logistycznego", float(cells["p_logit"].max())],
        ["Cells with response rate < 0.80", int((cells["response_rate"] < 0.80).sum())],
        ["Cells with response rate < 0.85", int((cells["response_rate"] < 0.85).sum())],
        ["Cells with response rate < 0.90", int((cells["response_rate"] < 0.90).sum())],
        ["Cells with n observed = 0", int((cells["n_observed"] == 0).sum())],
    ], columns=["miara", "wartosc"])

    # -------------------------------------------------------------------------
    # PROPENSITY MODEL — COEFFICIENTS
    # -------------------------------------------------------------------------

    conf = propensity_model.conf_int()
    model_coef = pd.DataFrame({
        "term": propensity_model.params.index,
        "B": propensity_model.params.values,
        "SE": propensity_model.bse.values,
        "z": propensity_model.tvalues.values,
        "p": propensity_model.pvalues.values,
        "OR": np.exp(propensity_model.params.values),
        "OR_CI95_low": np.exp(conf[0].values),
        "OR_CI95_high": np.exp(conf[1].values),
    })

    # -------------------------------------------------------------------------
    # MNAR — MISSING STUDENTS HAVE HFZ LOWER BY 0 / 5 / 10 / 20 p.p.
    # THAN OBSERVED STUDENTS IN THE SAME 320-CELL STRATUM
    # -------------------------------------------------------------------------

    mnar_nat_rows = []
    mnar_reg_rows = []

    baseline_reg = regional.set_index("wojewodztwo")["standardized_CC_pct"]

    for delta_pp in MNAR_DELTAS_PP:
        d = delta_pp / 100.0
        tmp = cells.copy()

        tmp["p_missing_assumed"] = (tmp["hfz_cc"] - d).clip(lower=0.0, upper=1.0)

        tmp["p_full_mnar"] = (
            tmp["n_hfz"]
            + tmp["n_missing"] * tmp["p_missing_assumed"]
        ) / tmp["n_eligible"]

        # National: hypothetical proportion in the full eligible population represented in the export.
        national = 100 * (
            (
                tmp["n_hfz"]
                + tmp["n_missing"] * tmp["p_missing_assumed"]
            ).sum()
            / tmp["n_eligible"].sum()
        )

        reg_scenario = []

        for voiv, g in tmp.groupby("wojewodztwo", sort=True):
            std_mnar = 100 * float(np.sum(g["std_weight"] * g["p_full_mnar"]))
            reg_scenario.append((voiv, std_mnar))

            mnar_reg_rows.append({
                "delta_missing_vs_observed_pp": -delta_pp,
                "wojewodztwo": voiv,
                "standardized_HFZ_MNAR_pct": std_mnar,
                "delta_vs_CC_std_pp": std_mnar - float(baseline_reg.loc[voiv]),
            })

        reg_df = pd.DataFrame(reg_scenario, columns=["wojewodztwo", "estimate"])
        ranks_cc = (
            regional.set_index("wojewodztwo")["rank_std_CC"]
            .reindex(reg_df["wojewodztwo"])
            .to_numpy()
        )
        ranks_scen = (
            reg_df["estimate"].rank(ascending=False, method="min").astype(int).to_numpy()
        )

        max_row = reg_df.loc[reg_df["estimate"].idxmax()]
        min_row = reg_df.loc[reg_df["estimate"].idxmin()]

        mnar_nat_rows.append({
            "delta_missing_vs_observed_pp": -delta_pp,
            "HFZ_kraj_pct": national,
            "delta_vs_CC_raw_pp": national - cc_overall,
            "max_woj": max_row["wojewodztwo"],
            "max_woj_pct": float(max_row["estimate"]),
            "min_woj": min_row["wojewodztwo"],
            "min_woj_pct": float(min_row["estimate"]),
            "regional_range_pp": float(max_row["estimate"] - min_row["estimate"]),
            "Spearman_vs_CC_regional": spearman_ranks(ranks_cc, ranks_scen),
            "max_abs_regional_change_pp": float(
                np.max(
                    np.abs(
                        reg_df.set_index("wojewodztwo")["estimate"]
                        - baseline_reg
                    )
                )
            ),
        })

    mnar_nat = pd.DataFrame(mnar_nat_rows)
    mnar_reg = pd.DataFrame(mnar_reg_rows)

    # -------------------------------------------------------------------------
    # MNAR FIGURE — NATIONAL
    # -------------------------------------------------------------------------

    fig, ax = plt.subplots(figsize=(7.0, 4.6))

    x = -mnar_nat["delta_missing_vs_observed_pp"].to_numpy()
    y = mnar_nat["HFZ_kraj_pct"].to_numpy()

    ax.plot(x, y, marker="o")
    ax.set_xlabel("Assumed HFZ deficit among students with missing 20mSRT (percentage points)")
    ax.set_ylabel("Estimated HFZ in eligible students (%)")
    ax.set_xticks([0, 5, 10, 20])
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()

    fig.savefig(FIG_MNAR_PNG, dpi=600, bbox_inches="tight")
    fig.savefig(FIG_MNAR_SVG, bbox_inches="tight")
    plt.close(fig)

    # -------------------------------------------------------------------------
    # ZAPIS
    # -------------------------------------------------------------------------

    cells.to_csv(F_CELLS, index=False, encoding="utf-8-sig")
    overall.to_csv(F_OVERALL, index=False, encoding="utf-8-sig")
    age_sex.to_csv(F_AGESEX, index=False, encoding="utf-8-sig")
    regional.to_csv(F_REGIONAL, index=False, encoding="utf-8-sig")
    positivity.to_csv(F_POSITIVITY, index=False, encoding="utf-8-sig")
    mnar_nat.to_csv(F_MNAR_NAT, index=False, encoding="utf-8-sig")
    mnar_reg.to_csv(F_MNAR_REG, index=False, encoding="utf-8-sig")
    model_coef.to_csv(F_MODEL, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(F_XLSX, engine="openpyxl") as writer:
        overall.to_excel(writer, sheet_name="IPW_ogolem", index=False)
        age_sex.to_excel(writer, sheet_name="IPW_wiek_plec", index=False)
        regional.to_excel(writer, sheet_name="IPW_woj_std", index=False)
        positivity.to_excel(writer, sheet_name="Positivity", index=False)
        cells.to_excel(writer, sheet_name="Komorki_320", index=False)
        mnar_nat.to_excel(writer, sheet_name="MNAR_kraj", index=False)
        mnar_reg.to_excel(writer, sheet_name="MNAR_woj", index=False)
        model_coef.to_excel(writer, sheet_name="Model_propensity", index=False)

    # -------------------------------------------------------------------------
    # RAPORT KONTROLNY
    # -------------------------------------------------------------------------

    # Ekstrema propensity/positivity
    min_resp_row = cells.loc[cells["response_rate"].idxmin()]
    max_resp_row = cells.loc[cells["response_rate"].idxmax()]

    # Regionalne ekstrema CC standaryzowanego
    max_std_row = regional.loc[regional["standardized_CC_pct"].idxmax()]
    min_std_row = regional.loc[regional["standardized_CC_pct"].idxmin()]

    lines = [
        "IPW + CELL CHECKS + POSITIVITY + MNAR — 20mSRT / HFZ, 2025",
        "=" * 104,
        "",
        f"Database: {db_path}",
        f"Table: {TABLE_ALL}",
        f"Age: {AGE_COL} — completed years on 30 April 2025",
        "Regional standard: national age × sex distribution of all eligible students aged 10–19",
        "before exclusion for missing 20mSRT.",
        "",
        "COUNTS",
        f"N source: {n_source:,}",
        f"N unique student_id: {n_unique:,}",
        f"N eligible ages 10–19 + valid sex code: {n_eligible:,}",
        f"N observed / complete-case: {n_final:,}",
        f"N missing 20mSRT: {n_missing:,}",
        f"Observed proportion: {100*p_observed:.6f}%",
        f"N voivodeships: {n_voiv}",
        f"N voivodeship × age × sex cells: {len(cells)}",
        "",
        "CHECKS",
        f"N source = {EXPECTED_SOURCE_N:,}: {'OK' if n_source == EXPECTED_SOURCE_N else 'CHECK'}",
        f"N eligible = {EXPECTED_ELIGIBLE_N:,}: {'OK' if n_eligible == EXPECTED_ELIGIBLE_N else 'CHECK'}",
        f"N final = {EXPECTED_FINAL_N:,}: {'OK' if n_final == EXPECTED_FINAL_N else 'CHECK'}",
        f"one student = one row: {'OK' if n_source == n_unique else 'ERROR'}",
        f"320 cells: {'OK' if len(cells) == EXPECTED_CELLS else 'ERROR'}",
        f"Eligible students with missing voivodeship: {n_missing_voiv}",
        "",
        "PROPENSITY MODEL",
        "R ~ C(wiek) + C(plec) + C(wojewodztwo) + C(wiek):C(plec)",
        f"Log-likelihood: {propensity_model.llf:.6f}",
        f"AIC: {propensity_model.aic:.6f}",
        f"Minimum p_hat: {cells['p_logit'].min():.6f}",
        f"Maximum p_hat: {cells['p_logit'].max():.6f}",
        "",
        "STABILIZED LOGISTIC WEIGHTS",
        f"Formula: P(R=1) / P(R=1|age,sex,voivodeship,age×sex)",
        f"P(R=1) = {p_observed:.9f}",
        f"Range before truncation: {cells['w_logit_raw'].min():.6f}–{cells['w_logit_raw'].max():.6f}",
        f"1st percentile: {log_q01:.6f}",
        f"99th percentile: {log_q99:.6f}",
        f"Range after truncation: {cells[log_trunc_col].min():.6f}–{cells[log_trunc_col].max():.6f}",
        f"Observations affected by truncation: {log_n_clip:,} ({log_pct_clip:.4f}%)",
        f"Effective N before truncation: {neff_logit_raw:,.2f}",
        f"Effective N after truncation: {neff_logit_trunc:,.2f}",
        "",
        "320-CELL WEIGHTS",
        f"Formula: P(R=1) / P(R=1|voivodeship×age×sex), where the denominator is observed/eligible within the cell",
        f"Range before truncation: {cells['w_cell_raw'].min():.6f}–{cells['w_cell_raw'].max():.6f}",
        f"1st percentile: {cell_q01:.6f}",
        f"99th percentile: {cell_q99:.6f}",
        f"Range after truncation: {cells[cell_trunc_col].min():.6f}–{cells[cell_trunc_col].max():.6f}",
        f"Observations affected by truncation: {cell_n_clip:,} ({cell_pct_clip:.4f}%)",
        f"Effective N after truncation: {neff_cell_trunc:,.2f}",
        "",
        "POSITIVITY",
        f"Minimum observed/eligible across 320 cells: {cells['response_rate'].min():.6f}",
        f"  cell: {min_resp_row['wojewodztwo']} | age {int(min_resp_row['wiek'])} | sex {min_resp_row['plec']} | "
        f"{int(min_resp_row['n_observed']):,}/{int(min_resp_row['n_eligible']):,}",
        f"Maximum observed/eligible across 320 cells: {cells['response_rate'].max():.6f}",
        f"  cell: {max_resp_row['wojewodztwo']} | age {int(max_resp_row['wiek'])} | sex {max_resp_row['plec']} | "
        f"{int(max_resp_row['n_observed']):,}/{int(max_resp_row['n_eligible']):,}",
        f"Minimum n_observed per cell: {int(cells['n_observed'].min()):,}",
        f"Cells with n_observed = 0: {int((cells['n_observed']==0).sum())}",
        "",
        "HFZ OVERALL — CRUDE ESTIMAND",
        f"CC: {cc_overall:.6f}%",
        f"Logistic IPW, after truncation: {logit_tr_overall:.6f}% "
        f"(Δ {logit_tr_overall-cc_overall:+.6f} p.p.)",
        f"Cell IPW, after truncation: {cell_tr_overall:.6f}% "
        f"(Δ {cell_tr_overall-cc_overall:+.6f} p.p.)",
        "",
        "MAIN REGIONAL ESTIMAND — STANDARDIZED TO THE NATIONAL AGE × SEX DISTRIBUTION",
        f"Max |logistic IPW - CC|: {max_std_diff_logit:.12f} p.p.",
        f"Max |cell IPW - CC|: {max_std_diff_cell:.12f} p.p.",
        "Values near 0 are expected because the IPW weight is constant within",
        "each voivodeship×age×sex cell and does not change the within-cell HFZ proportion.",
        f"CC max: {max_std_row['wojewodztwo']} {max_std_row['standardized_CC_pct']:.6f}%",
        f"CC min: {min_std_row['wojewodztwo']} {min_std_row['standardized_CC_pct']:.6f}%",
        "",
        "MNAR SCENARIOS",
        "Assumption: missing students have lower HFZ probability than observed students in the same 320-cell stratum.",
        mnar_nat.to_string(index=False),
        "",
        "FILES",
        str(F_OVERALL),
        str(F_AGESEX),
        str(F_REGIONAL),
        str(F_POSITIVITY),
        str(F_MNAR_NAT),
        str(F_MNAR_REG),
        str(F_XLSX),
        str(FIG_MNAR_PNG),
        str(FIG_MNAR_SVG),
    ]

    F_CONTROL.write_text("\n".join(lines), encoding="utf-8")

    con.close()

    print("\nDONE")
    print(f"N eligible:        {n_eligible:,}")
    print(f"N complete-case:   {n_final:,}")
    print(f"CC overall HFZ:    {cc_overall:.4f}%")
    print(f"Logit IPW HFZ:     {logit_tr_overall:.4f}%")
    print(f"Cell IPW HFZ:      {cell_tr_overall:.4f}%")
    print(f"Max std Δ logit:   {max_std_diff_logit:.12f} p.p.")
    print(f"Max std Δ cell:    {max_std_diff_cell:.12f} p.p.")
    print("")
    print(f"Raport kontrolny:  {F_CONTROL}")
    print(f"Excel:              {F_XLSX}")
    print(f"MNAR figure:        {FIG_MNAR_PNG}")
    print("=" * 104)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nERROR:", exc)
        sys.exit(1)
