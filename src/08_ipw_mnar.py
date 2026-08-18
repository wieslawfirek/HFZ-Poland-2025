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
# IPW + KONTROLA KOMÓRKOWA + POSITIVITY + MNAR — 20mSRT / HFZ, 2025
#
# Analiza wykonuje:
# 1) model prawdopodobieństwa dostępności 20mSRT:
#       R ~ wiek + płeć + województwo + wiek×płeć
# 2) stabilizowane IPW = P(R=1) / P(R=1|X)
# 3) ograniczenie wag do 1. i 99. percentyla
# 4) alternatywne wagi bezpośrednio w 320 komórkach:
#       województwo × wiek × płeć
# 5) diagnostykę positivity
# 6) porównanie:
#       - surowy complete-case vs IPW
#       - STANDARYZOWANY HFZ complete-case vs STANDARYZOWANY HFZ po IPW
#         przy wspólnym standardzie B
# 7) scenariusze MNAR:
#       brakujący uczniowie mają prawdopodobieństwo HFZ niższe o
#       5, 10 lub 20 punktów procentowych niż obserwowani uczniowie
#       w tej samej komórce województwo × wiek × płeć.
#
# UWAGA:
# Przy modelu IPW opartym wyłącznie na województwie, wieku i płci waga jest
# stała w każdej z 320 komórek. Dlatego po bezpośredniej standaryzacji
# regionalnej do tych samych 20 komórek wieku × płci IPW nie zmienia
# wewnątrzkomórkowego odsetka HFZ. Skrypt oblicza to jawnie i sprawdza.
# Nie jest to błąd — to konsekwencja konstrukcji estymandu.
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
F_XLSX = OUT_DIR / "analiza_IPW_MNAR_2025.xlsx"
F_CONTROL = OUT_DIR / "analiza_IPW_MNAR_2025_kontrola.txt"

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
            "Nie znaleziono finalnej bazy HFZ w folderze Baza.\n"
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
    w której każda wartość występuje 'counts' razy, bez rozwijania milionów wierszy.
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


def std_rate_and_ci(group: pd.DataFrame, rate_col: str, std_weight_col: str = "std_B"):
    p = group[rate_col].to_numpy(dtype=float)
    n = group["n_observed"].to_numpy(dtype=float)
    w = group[std_weight_col].to_numpy(dtype=float)

    est = float(np.sum(w * p))

    # Ponieważ wagi IPW są stałe wewnątrz komórki woj×wiek×płeć,
    # efektywna liczebność wewnątrzkomórkowa pozostaje n_observed.
    var = float(np.sum((w ** 2) * p * (1 - p) / n))
    se = math.sqrt(max(var, 0.0))
    lo = max(0.0, est - 1.96 * se)
    hi = min(1.0, est + 1.96 * se)

    return 100 * est, 100 * lo, 100 * hi


def spearman_ranks(a, b):
    return float(pd.Series(a).corr(pd.Series(b), method="spearman"))


# =============================================================================
# ANALIZA
# =============================================================================

def main():
    print("=" * 104)
    print("IPW + KONTROLA KOMÓRKOWA + POSITIVITY + MNAR — 20mSRT / HFZ, 2025")
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
            f"Brak wymaganych kolumn: {missing_cols}\nDostępne: {cols}"
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
    # LICZEBNOŚCI I KONTROLE
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
            f"1 uczeń != 1 rekord: N={n_source:,}, unique={n_unique:,}"
        )
    if n_missing_voiv > 0:
        raise RuntimeError(
            f"{n_missing_voiv:,} kwalifikowanych uczniów nie ma województwa. "
            "Nie można poprawnie wykonać obecnego modelu IPW."
        )

    # -------------------------------------------------------------------------
    # 320 KOMÓREK: województwo × wiek × płeć
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
            f"Oczekiwano {EXPECTED_CELLS} komórek, otrzymano {len(cells)}."
        )
    if n_voiv != EXPECTED_VOIV:
        raise RuntimeError(
            f"Oczekiwano {EXPECTED_VOIV} województw, otrzymano {n_voiv}."
        )
    if (cells["n_observed"] <= 0).any():
        raise RuntimeError("Naruszenie positivity: co najmniej jedna komórka ma 0 obserwowanych 20mSRT.")

    # -------------------------------------------------------------------------
    # STANDARD B: krajowa struktura wieku × płci PRZED wyłączeniem braków
    # -------------------------------------------------------------------------

    std_B = (
        cells.groupby(["wiek", "plec"], as_index=False)
        .agg(n_standard_B=("n_eligible", "sum"))
    )
    std_B["std_B"] = std_B["n_standard_B"] / std_B["n_standard_B"].sum()

    cells = cells.merge(
        std_B[["wiek", "plec", "std_B"]],
        on=["wiek", "plec"],
        how="left",
        validate="m:1",
    )

    # -------------------------------------------------------------------------
    # MODEL LOGISTYCZNY PROPENSITY
    # R ~ wiek + płeć + województwo + wiek×płeć
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
        raise RuntimeError("Model propensity wygenerował p <= 0.")

    # Stabilizowana waga = marginalne P(R=1) / warunkowe P(R=1|X)
    cells["w_logit_raw"] = p_observed / cells["p_logit"]

    # -------------------------------------------------------------------------
    # ALTERNATYWNE WAGI KOMÓRKOWE
    # -------------------------------------------------------------------------

    cells["p_cell"] = cells["response_rate"]
    cells["w_cell_raw"] = p_observed / cells["p_cell"]

    # -------------------------------------------------------------------------
    # TRUNCATION / WINSORIZATION 1.–99. PERCENTYL
    # percentyle liczone po obserwowanych osobach, nie po 320 unikalnych wagach
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
    # DIAGNOSTYKA WAG
    # -------------------------------------------------------------------------

    neff_logit_raw = effective_n(cells["w_logit_raw"], cells["n_observed"])
    neff_logit_trunc = effective_n(cells[log_trunc_col], cells["n_observed"])

    neff_cell_raw = effective_n(cells["w_cell_raw"], cells["n_observed"])
    neff_cell_trunc = effective_n(cells[cell_trunc_col], cells["n_observed"])

    # -------------------------------------------------------------------------
    # WYNIK OGÓLNY
    # -------------------------------------------------------------------------

    cc_overall = raw_hfz(cells)
    logit_raw_overall = weighted_hfz(cells, "w_logit_raw")
    logit_tr_overall = weighted_hfz(cells, log_trunc_col)
    cell_raw_overall = weighted_hfz(cells, "w_cell_raw")
    cell_tr_overall = weighted_hfz(cells, cell_trunc_col)

    overall = pd.DataFrame([
        {
            "analiza": "Complete-case",
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
            "analiza": "IPW logistyczne — surowe wagi",
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
            "analiza": "IPW logistyczne — 1.–99. percentyl",
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
            "analiza": "IPW komórkowe — surowe wagi",
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
            "analiza": "IPW komórkowe — 1.–99. percentyl",
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
    # WIEK × PŁEĆ — CC, IPW logistyczne i IPW komórkowe
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
    # WOJEWÓDZTWA — SUROWE ORAZ GŁÓWNY ESTYMAND STANDARYZOWANY
    # -------------------------------------------------------------------------

    regional_rows = []

    for voiv, g in cells.groupby("wojewodztwo", sort=True):
        # Surowe — mogą zmieniać się po IPW
        raw_cc = raw_hfz(g)
        raw_logit = weighted_hfz(g, log_trunc_col)
        raw_cell = weighted_hfz(g, cell_trunc_col)

        # W komórce woj×wiek×płeć waga jest stała, więc ważony HFZ komórkowy
        # jest matematycznie równy complete-case HFZ komórkowemu.
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
            "std_B_CC_pct": std_cc,
            "std_B_CC_CI95_low": ci_lo,
            "std_B_CC_CI95_high": ci_hi,
            "std_B_IPW_logit_pct": std_logit,
            "std_delta_logit_pp": std_logit - std_cc,
            "std_B_IPW_cell_pct": std_cell,
            "std_delta_cell_pp": std_cell - std_cc,
        })

    regional = pd.DataFrame(regional_rows)

    regional["rank_std_CC"] = (
        regional["std_B_CC_pct"].rank(ascending=False, method="min").astype(int)
    )
    regional["rank_std_IPW_logit"] = (
        regional["std_B_IPW_logit_pct"].rank(ascending=False, method="min").astype(int)
    )
    regional["rank_std_IPW_cell"] = (
        regional["std_B_IPW_cell_pct"].rank(ascending=False, method="min").astype(int)
    )

    regional = regional.sort_values("std_B_CC_pct", ascending=False).reset_index(drop=True)

    max_std_diff_logit = float(regional["std_delta_logit_pp"].abs().max())
    max_std_diff_cell = float(regional["std_delta_cell_pp"].abs().max())

    # -------------------------------------------------------------------------
    # POSITIVITY
    # -------------------------------------------------------------------------

    positivity = pd.DataFrame([
        ["Liczba komórek", len(cells)],
        ["Minimalne n eligible w komórce", int(cells["n_eligible"].min())],
        ["Minimalne n observed w komórce", int(cells["n_observed"].min())],
        ["Maksymalne n eligible w komórce", int(cells["n_eligible"].max())],
        ["Minimalny rzeczywisty response rate", float(cells["response_rate"].min())],
        ["Maksymalny rzeczywisty response rate", float(cells["response_rate"].max())],
        ["Minimalne p z modelu logistycznego", float(cells["p_logit"].min())],
        ["Maksymalne p z modelu logistycznego", float(cells["p_logit"].max())],
        ["Komórki z response rate < 0,80", int((cells["response_rate"] < 0.80).sum())],
        ["Komórki z response rate < 0,85", int((cells["response_rate"] < 0.85).sum())],
        ["Komórki z response rate < 0,90", int((cells["response_rate"] < 0.90).sum())],
        ["Komórki z n observed = 0", int((cells["n_observed"] == 0).sum())],
    ], columns=["miara", "wartosc"])

    # -------------------------------------------------------------------------
    # MODEL PROPENSITY — WSPÓŁCZYNNIKI
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
    # MNAR — BRAKUJĄCY MAJĄ HFZ NIŻSZE O 0 / 5 / 10 / 20 p.p.
    # NIŻ OBSERWOWANI W TEJ SAMEJ KOMÓRCE 320
    # -------------------------------------------------------------------------

    mnar_nat_rows = []
    mnar_reg_rows = []

    baseline_reg = regional.set_index("wojewodztwo")["std_B_CC_pct"]

    for delta_pp in MNAR_DELTAS_PP:
        d = delta_pp / 100.0
        tmp = cells.copy()

        tmp["p_missing_assumed"] = (tmp["hfz_cc"] - d).clip(lower=0.0, upper=1.0)

        tmp["p_full_mnar"] = (
            tmp["n_hfz"]
            + tmp["n_missing"] * tmp["p_missing_assumed"]
        ) / tmp["n_eligible"]

        # Kraj: hipotetyczny odsetek w całej populacji eligible w eksporcie.
        national = 100 * (
            (
                tmp["n_hfz"]
                + tmp["n_missing"] * tmp["p_missing_assumed"]
            ).sum()
            / tmp["n_eligible"].sum()
        )

        reg_scenario = []

        for voiv, g in tmp.groupby("wojewodztwo", sort=True):
            std_mnar = 100 * float(np.sum(g["std_B"] * g["p_full_mnar"]))
            reg_scenario.append((voiv, std_mnar))

            mnar_reg_rows.append({
                "delta_missing_vs_observed_pp": -delta_pp,
                "wojewodztwo": voiv,
                "std_B_HFZ_MNAR_pct": std_mnar,
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
    # RYCINA MNAR — KRAJ
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
    max_std_row = regional.loc[regional["std_B_CC_pct"].idxmax()]
    min_std_row = regional.loc[regional["std_B_CC_pct"].idxmin()]

    lines = [
        "IPW + KONTROLA KOMÓRKOWA + POSITIVITY + MNAR — 20mSRT / HFZ, 2025",
        "=" * 104,
        "",
        f"Baza: {db_path}",
        f"Tabela: {TABLE_ALL}",
        f"Wiek: {AGE_COL} — pełne lata na 30.04.2025",
        "",
        "LICZEBNOŚCI",
        f"N źródłowe: {n_source:,}",
        f"N unikalnych student_id: {n_unique:,}",
        f"N eligible 10–19 + prawidłowa płeć: {n_eligible:,}",
        f"N observed / complete-case: {n_final:,}",
        f"N missing 20mSRT: {n_missing:,}",
        f"Odsetek observed: {100*p_observed:.6f}%",
        f"N województw: {n_voiv}",
        f"N komórek woj×wiek×płeć: {len(cells)}",
        "",
        "KONTROLE",
        f"N źródłowe = {EXPECTED_SOURCE_N:,}: {'OK' if n_source == EXPECTED_SOURCE_N else 'UWAGA'}",
        f"N eligible = {EXPECTED_ELIGIBLE_N:,}: {'OK' if n_eligible == EXPECTED_ELIGIBLE_N else 'UWAGA'}",
        f"N final = {EXPECTED_FINAL_N:,}: {'OK' if n_final == EXPECTED_FINAL_N else 'UWAGA'}",
        f"1 uczeń = 1 rekord: {'OK' if n_source == n_unique else 'BŁĄD'}",
        f"320 komórek: {'OK' if len(cells) == EXPECTED_CELLS else 'BŁĄD'}",
        f"Brak województwa w eligible: {n_missing_voiv}",
        "",
        "MODEL PROPENSITY",
        "R ~ C(wiek) + C(plec) + C(wojewodztwo) + C(wiek):C(plec)",
        f"Log-likelihood: {propensity_model.llf:.6f}",
        f"AIC: {propensity_model.aic:.6f}",
        f"Minimalne p_hat: {cells['p_logit'].min():.6f}",
        f"Maksymalne p_hat: {cells['p_logit'].max():.6f}",
        "",
        "STABILIZOWANE WAGI LOGISTYCZNE",
        f"Wzór: P(R=1) / P(R=1|wiek,płeć,województwo,wiek×płeć)",
        f"P(R=1) = {p_observed:.9f}",
        f"Zakres przed truncation: {cells['w_logit_raw'].min():.6f}–{cells['w_logit_raw'].max():.6f}",
        f"1. percentyl: {log_q01:.6f}",
        f"99. percentyl: {log_q99:.6f}",
        f"Zakres po truncation: {cells[log_trunc_col].min():.6f}–{cells[log_trunc_col].max():.6f}",
        f"Obserwacje dotknięte truncation: {log_n_clip:,} ({log_pct_clip:.4f}%)",
        f"Efektywne N przed truncation: {neff_logit_raw:,.2f}",
        f"Efektywne N po truncation: {neff_logit_trunc:,.2f}",
        "",
        "WAGI KOMÓRKOWE 320",
        f"Wzór: P(R=1) / P(R=1|województwo×wiek×płeć), gdzie mianownik = observed/eligible w komórce",
        f"Zakres przed truncation: {cells['w_cell_raw'].min():.6f}–{cells['w_cell_raw'].max():.6f}",
        f"1. percentyl: {cell_q01:.6f}",
        f"99. percentyl: {cell_q99:.6f}",
        f"Zakres po truncation: {cells[cell_trunc_col].min():.6f}–{cells[cell_trunc_col].max():.6f}",
        f"Obserwacje dotknięte truncation: {cell_n_clip:,} ({cell_pct_clip:.4f}%)",
        f"Efektywne N po truncation: {neff_cell_trunc:,.2f}",
        "",
        "POSITIVITY",
        f"Minimalny observed/eligible w 320 komórkach: {cells['response_rate'].min():.6f}",
        f"  komórka: {min_resp_row['wojewodztwo']} | wiek {int(min_resp_row['wiek'])} | płeć {min_resp_row['plec']} | "
        f"{int(min_resp_row['n_observed']):,}/{int(min_resp_row['n_eligible']):,}",
        f"Maksymalny observed/eligible w 320 komórkach: {cells['response_rate'].max():.6f}",
        f"  komórka: {max_resp_row['wojewodztwo']} | wiek {int(max_resp_row['wiek'])} | płeć {max_resp_row['plec']} | "
        f"{int(max_resp_row['n_observed']):,}/{int(max_resp_row['n_eligible']):,}",
        f"Minimalne n_observed w komórce: {int(cells['n_observed'].min()):,}",
        f"Komórki z n_observed = 0: {int((cells['n_observed']==0).sum())}",
        "",
        "HFZ OGÓŁEM — SUROWY ESTYMAND",
        f"CC: {cc_overall:.6f}%",
        f"IPW logistyczne, po truncation: {logit_tr_overall:.6f}% "
        f"(Δ {logit_tr_overall-cc_overall:+.6f} p.p.)",
        f"IPW komórkowe, po truncation: {cell_tr_overall:.6f}% "
        f"(Δ {cell_tr_overall-cc_overall:+.6f} p.p.)",
        "",
        "GŁÓWNY ESTYMAND REGIONALNY — STANDARYZOWANY DO STANDARDU B",
        f"Maks. |IPW logistyczne - CC|: {max_std_diff_logit:.12f} p.p.",
        f"Maks. |IPW komórkowe - CC|: {max_std_diff_cell:.12f} p.p.",
        "Jeżeli wartości są ~0, jest to oczekiwane: waga IPW jest stała wewnątrz",
        "każdej komórki województwo×wiek×płeć i nie zmienia odsetka HFZ w tej komórce.",
        f"CC max: {max_std_row['wojewodztwo']} {max_std_row['std_B_CC_pct']:.6f}%",
        f"CC min: {min_std_row['wojewodztwo']} {min_std_row['std_B_CC_pct']:.6f}%",
        "",
        "SCENARIUSZE MNAR",
        "Założenie: brakujący mają HFZ niższe od obserwowanych w tej samej komórce 320.",
        mnar_nat.to_string(index=False),
        "",
        "PLIKI",
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

    print("\nGOTOWE")
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
        print("\nBŁĄD:", exc)
        sys.exit(1)
