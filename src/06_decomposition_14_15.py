from __future__ import annotations

import sys
from pathlib import Path
import duckdb
import pandas as pd

ROOT = Path.cwd()
BAZA = ROOT / "data" / "derived"
OUT = ROOT / "results" / "06_decomposition_14_15"
OUT.mkdir(parents=True, exist_ok=True)

DB_GLOB = "sportowe_talenty_2025_HFZ_po_nowym_wieku*.duckdb"
TABLE = "uczniowie2025_hfz_final_10_19"

OUT_CSV = OUT / "HFZ_14_15_dekompozycja_progu_i_wyniku.csv"
OUT_TXT = OUT / "HFZ_14_15_dekompozycja_kontrola.txt"

THRESHOLDS = {
    "dz": {14: 27, 15: 30},
    "ch": {14: 36, 15: 42},
}

def newest_db():
    files = list(BAZA.glob(DB_GLOB))
    if not files:
        raise FileNotFoundError(
            f"No database found in {BAZA} matching {DB_GLOB}"
        )
    return max(files, key=lambda p: p.stat().st_mtime)

def q(name):
    return '"' + name.replace('"', '""') + '"'

def choose(cols, candidates):
    lookup = {c.lower(): c for c in cols}
    for c in candidates:
        if c.lower() in lookup:
            return lookup[c.lower()]
    raise KeyError(f"Column not found. Searched for: {candidates}. Available: {cols}")

def main():
    db = newest_db()
    con = duckdb.connect(str(db), read_only=True)

    objs = con.execute("SHOW TABLES").df()["name"].astype(str).tolist()
    if TABLE not in objs:
        # SHOW TABLES may not return a view in every version; try DESCRIBE directly
        try:
            con.execute(f"DESCRIBE {q(TABLE)}").df()
        except Exception:
            raise RuntimeError(f"Not found {TABLE}. Available: {objs}")

    cols = con.execute(f"DESCRIBE {q(TABLE)}").df()["column_name"].astype(str).tolist()
    age_col = choose(cols, ["wiek_fitnessgram"])
    sex_col = choose(cols, ["plec", "sex", "sex"])
    beep_col = choose(cols, ["beep", "pacer", "20msrt"])

    rows = []

    for sex in ["dz", "ch"]:
        t14 = THRESHOLDS[sex][14]
        t15 = THRESHOLDS[sex][15]

        # Select only 14- and 15-year-olds of this sex
        stats = con.execute(
            f"""
            SELECT
                TRY_CAST({q(age_col)} AS INTEGER) AS age,
                COUNT(*) AS n,
                AVG(TRY_CAST({q(beep_col)} AS DOUBLE)) AS mean_beep,
                MEDIAN(TRY_CAST({q(beep_col)} AS DOUBLE)) AS median_beep,

                AVG(
                    CASE WHEN TRY_CAST({q(beep_col)} AS DOUBLE) >= {t14}
                    THEN 1.0 ELSE 0.0 END
                ) AS p_at_t14,

                AVG(
                    CASE WHEN TRY_CAST({q(beep_col)} AS DOUBLE) >= {t15}
                    THEN 1.0 ELSE 0.0 END
                ) AS p_at_t15

            FROM {q(TABLE)}
            WHERE CAST({q(sex_col)} AS VARCHAR) = '{sex}'
              AND TRY_CAST({q(age_col)} AS INTEGER) IN (14, 15)
              AND TRY_CAST({q(beep_col)} AS DOUBLE) IS NOT NULL
            GROUP BY 1
            ORDER BY 1
            """
        ).df()

        if set(stats["age"]) != {14, 15}:
            raise RuntimeError(f"Missing age-14/15 data for sex {sex}")

        r14 = stats.loc[stats["age"] == 14].iloc[0]
        r15 = stats.loc[stats["age"] == 15].iloc[0]

        # Obserwowane:
        # 14-latkowie oceniani progiem 14 lat; 15-latkowie progiem 15 lat.
        p14_obs = float(r14["p_at_t14"])
        p15_obs = float(r15["p_at_t15"])

        # Kontrfaktyczne:
        # Age-15 students are evaluated using the same threshold as age-14 students.
        p15_if_t14 = float(r15["p_at_t14"])

        # Decomposition using the age-14 threshold as the reference:
        # observed drop = performance/distribution component + threshold component
        performance_component = p15_if_t14 - p14_obs
        threshold_component = p15_obs - p15_if_t14
        observed_change = p15_obs - p14_obs

        # Sum check.
        residual = observed_change - (performance_component + threshold_component)

        total_abs = abs(observed_change)
        threshold_share = (
            abs(threshold_component) / total_abs * 100 if total_abs > 0 else None
        )
        performance_share = (
            abs(performance_component) / total_abs * 100 if total_abs > 0 else None
        )

        rows.append({
            "plec": sex,
            "n_14": int(r14["n"]),
            "n_15": int(r15["n"]),
            "prog_14": t14,
            "prog_15": t15,
            "srednia_20mSRT_14": round(float(r14["mean_beep"]), 3),
            "srednia_20mSRT_15": round(float(r15["mean_beep"]), 3),
            "mediana_20mSRT_14": round(float(r14["median_beep"]), 3),
            "mediana_20mSRT_15": round(float(r15["median_beep"]), 3),

            "HFZ_14_obserwowane_pct": round(100*p14_obs, 3),
            "HFZ_15_obserwowane_pct": round(100*p15_obs, 3),
            "HFZ_15_gdyby_prog_14_pct": round(100*p15_if_t14, 3),

            "zmiana_obserwowana_pp": round(100*observed_change, 3),
            "komponent_roznicy_wynikow_pp": round(100*performance_component, 3),
            "komponent_zmiany_progu_pp": round(100*threshold_component, 3),
            "kontrola_reszta_pp": round(100*residual, 6),

            "udzial_zmiany_progu_w_abs_spadku_pct": (
                round(threshold_share, 1) if threshold_share is not None else None
            ),
            "udzial_roznicy_wynikow_w_abs_spadku_pct": (
                round(performance_share, 1) if performance_share is not None else None
            ),
        })

    out = pd.DataFrame(rows)
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    lines = [
        "DECOMPOSITION OF THE HFZ DIFFERENCE BETWEEN AGES 14 AND 15",
        "=" * 86,
        "",
        f"Database: {db}",
        f"Table: {TABLE}",
        "",
        "Definicja:",
        "Observed change = HFZ(age 15, age-15 threshold) - HFZ(age 14, age-14 threshold).",
        "Score-distribution component = HFZ(age 15, age-14 threshold) - HFZ(age 14, age-14 threshold).",
        "Threshold-change component = HFZ(age 15, age-15 threshold) - HFZ(age 15, age-14 threshold).",
        "The two components sum exactly to the observed change.",
        "",
        out.to_string(index=False),
    ]
    OUT_TXT.write_text("\n".join(lines), encoding="utf-8")

    con.close()

    print("=" * 86)
    print("DONE")
    print(out.to_string(index=False))
    print()
    print(OUT_CSV)
    print(OUT_TXT)
    print("=" * 86)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\nERROR:", e)
        sys.exit(1)
