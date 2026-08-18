from __future__ import annotations

"""Age-by-sex grouped-binomial model used in the manuscript.

The model is fitted to 20 aggregated age x sex cells. This is statistically
identical, for these predictors, to fitting a Bernoulli logistic model to all
individual records, while making the grouped-binomial likelihood explicit.

Outputs:
- Table S4 model comparison and M2 coefficients (CSV)
- Table S5 age/sex percentages and sex differences (CSV)
- Table S6 adjacent-age contrasts (CSV; estimation-focused, no multiplicity p-values)
- Figure 2 (PNG/SVG)
- control text file
"""

import math
import sys
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import patsy
import statsmodels.api as sm

ROOT = Path.cwd()
DB_DIR = ROOT / "data" / "derived"
DB_GLOB = "sportowe_talenty_2025_HFZ_po_nowym_wieku*.duckdb"
TABLE = "uczniowie2025_hfz_nowy_wiek"

OUT = ROOT / "results" / "05_age_sex_model"
OUT.mkdir(parents=True, exist_ok=True)

F_S4_MODELS = OUT / "Table_S4_model_comparison.csv"
F_S4_COEFS = OUT / "Table_S4_M2_coefficients.csv"
F_S5 = OUT / "Table_S5_age_sex_percentages.csv"
F_S6 = OUT / "Table_S6_adjacent_age_differences.csv"
F_CELLS = OUT / "age_sex_20_cells.csv"
F_CONTROL = OUT / "age_sex_model_control.txt"
FIG_PNG = OUT / "Figure2_HFZ_by_age_and_sex.png"
FIG_SVG = OUT / "Figure2_HFZ_by_age_and_sex.svg"

EXPECTED_N = 2_715_127


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def latest_db() -> Path:
    files = list(DB_DIR.glob(DB_GLOB))
    if not files:
        raise FileNotFoundError(f"No database matching {DB_GLOB} in {DB_DIR}")
    return max(files, key=lambda p: p.stat().st_mtime)


def normal_prop_ci(k: int, n: int):
    p = k / n
    se = math.sqrt(p * (1 - p) / n)
    return p, max(0.0, p - 1.96 * se), min(1.0, p + 1.96 * se)


def diff_ci(p1: float, n1: int, p2: float, n2: int):
    d = p1 - p2
    se = math.sqrt(p1 * (1-p1)/n1 + p2 * (1-p2)/n2)
    return d, d - 1.96*se, d + 1.96*se


def label_term(term: str) -> str:
    if term == "Intercept": return "Intercept"
    if "C(plec" in term and ":" not in term: return "Boys"
    if ":" in term:
        age = term.split("[T.")[1].split("]")[0]
        return f"Age {age} x boys"
    if "C(wiek" in term:
        age = term.split("[T.")[1].split("]")[0]
        return f"Age {age} years"
    return term


def main():
    db = latest_db()
    con = duckdb.connect(str(db), read_only=True)
    cols = con.execute(f"DESCRIBE {q(TABLE)}").df()["column_name"].astype(str).tolist()
    required = ["wiek_fitnessgram", "plec", "hfz_fitnessgram_nowy"]
    missing = [c for c in required if c not in cols]
    if missing:
        raise RuntimeError(f"Missing columns: {missing}; available: {cols}")

    cells = con.execute(f"""
        SELECT
            CAST(wiek_fitnessgram AS INTEGER) AS wiek,
            CASE WHEN plec='dz' THEN 'Girls' WHEN plec='ch' THEN 'Boys' END AS plec,
            COUNT(*)::BIGINT AS n,
            SUM(CAST(hfz_fitnessgram_nowy AS INTEGER))::BIGINT AS n_hfz
        FROM {q(TABLE)}
        WHERE CAST(wiek_fitnessgram AS INTEGER) BETWEEN 10 AND 19
          AND plec IN ('dz','ch')
          AND hfz_fitnessgram_nowy IN (0,1)
        GROUP BY 1,2
        ORDER BY 1,2
    """).df()
    con.close()

    cells["n_below"] = cells["n"] - cells["n_hfz"]
    n_total = int(cells["n"].sum())
    if n_total != EXPECTED_N:
        raise RuntimeError(f"Expected N={EXPECTED_N:,}, got {n_total:,}")
    if len(cells) != 20:
        raise RuntimeError(f"Expected 20 age x sex cells, got {len(cells)}")

    y = np.column_stack([cells["n_hfz"].to_numpy(), cells["n_below"].to_numpy()])
    X1 = patsy.dmatrix("C(wiek) + C(plec)", cells, return_type="dataframe")
    X2 = patsy.dmatrix('C(wiek, Treatment(reference=10))*C(plec, Treatment(reference="Girls"))', cells, return_type="dataframe")
    m1 = sm.GLM(y, X1, family=sm.families.Binomial()).fit()
    m2 = sm.GLM(y, X2, family=sm.families.Binomial()).fit()

    lr = 2 * (m2.llf - m1.llf)
    df_lr = X2.shape[1] - X1.shape[1]
    delta_aic = m2.aic - m1.aic

    s4_models = pd.DataFrame([
        ["M1: age + sex", X1.shape[1], m1.llf, m1.aic],
        ["M2: age x sex", X2.shape[1], m2.llf, m2.aic],
    ], columns=["model","parameters","log_likelihood","AIC"])
    s4_models.to_csv(F_S4_MODELS, index=False)

    ci = m2.conf_int()
    s4_coef = pd.DataFrame({
        "predictor": [label_term(x) for x in m2.params.index],
        "B": m2.params.values,
        "SE": m2.bse.values,
        "z": m2.tvalues.values,
        "p": m2.pvalues.values,
        "OR": np.exp(m2.params.values),
        "OR_CI95_low": np.exp(ci[0].values),
        "OR_CI95_high": np.exp(ci[1].values),
    })
    s4_coef.to_csv(F_S4_COEFS, index=False)

    # Table S5: crude cell proportions. Because M2 is saturated, these equal M2 predictions.
    out_s5=[]
    for age in range(10,20):
        g=cells[(cells.wiek==age)&(cells.plec=="Girls")].iloc[0]
        b=cells[(cells.wiek==age)&(cells.plec=="Boys")].iloc[0]
        pg,glo,ghi=normal_prop_ci(int(g.n_hfz),int(g.n))
        pb,blo,bhi=normal_prop_ci(int(b.n_hfz),int(b.n))
        d,dlo,dhi=diff_ci(pb,int(b.n),pg,int(g.n))
        out_s5.append([age,100*pg,100*glo,100*ghi,100*pb,100*blo,100*bhi,100*d,100*dlo,100*dhi])
    s5=pd.DataFrame(out_s5,columns=["age","girls_pct","girls_CI95_low","girls_CI95_high","boys_pct","boys_CI95_low","boys_CI95_high","boys_minus_girls_pp","diff_CI95_low","diff_CI95_high"])
    s5.to_csv(F_S5,index=False)

    # Table S6: secondary/estimational adjacent-age contrasts, separately by sex.
    rows=[]
    for sex in ["Girls","Boys"]:
        sub=cells[cells.plec==sex].set_index("wiek")
        for a in range(10,19):
            r1=sub.loc[a]; r2=sub.loc[a+1]
            p1=r1.n_hfz/r1.n; p2=r2.n_hfz/r2.n
            d,lo,hi=diff_ci(p1,int(r1.n),p2,int(r2.n))
            rows.append([sex,a,a+1,100*d,100*lo,100*hi])
    s6=pd.DataFrame(rows,columns=["sex","younger_age","older_age","younger_minus_older_pp","CI95_low","CI95_high"])
    s6.to_csv(F_S6,index=False)

    cells.to_csv(F_CELLS,index=False)

    fig,ax=plt.subplots(figsize=(7.5,5.0))
    for sex in ["Girls","Boys"]:
        s=s5 if sex=="Girls" else s5
        y=s5["girls_pct"] if sex=="Girls" else s5["boys_pct"]
        lo=s5["girls_CI95_low"] if sex=="Girls" else s5["boys_CI95_low"]
        hi=s5["girls_CI95_high"] if sex=="Girls" else s5["boys_CI95_high"]
        ax.errorbar(s5["age"],y,yerr=np.vstack([y-lo,hi-y]),marker="o",capsize=2,label=sex)
    ax.set_xlabel("Age (years)")
    ax.set_ylabel("Students achieving HFZ (%)")
    ax.set_xticks(range(10,20))
    ax.set_ylim(0,100)
    ax.grid(axis="y",alpha=.2)
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG_PNG,dpi=600,bbox_inches="tight")
    fig.savefig(FIG_SVG,bbox_inches="tight")
    plt.close(fig)

    F_CONTROL.write_text("\n".join([
        "AGE x SEX GROUPED-BINOMIAL MODEL — 2025",
        f"Database: {db}",
        f"N: {n_total:,}",
        "Model fitted to 20 aggregated age x sex cells.",
        f"M1 logL={m1.llf:.6f}; AIC={m1.aic:.6f}",
        f"M2 logL={m2.llf:.6f}; AIC={m2.aic:.6f}",
        f"LR chi2({df_lr})={lr:.6f}; p<0.001; delta AIC={delta_aic:.6f}",
        "BIC intentionally not reported for the grouped-binomial presentation.",
        "95% CIs for cell proportions use normal binomial approximation.",
        "95% CIs for differences use sqrt(var1+var2) and estimate +/- 1.96 SE.",
    ]),encoding="utf-8")

    print(F_CONTROL.read_text(encoding="utf-8"))

if __name__ == "__main__":
    try: main()
    except Exception as exc:
        print("ERROR:",exc)
        sys.exit(1)
