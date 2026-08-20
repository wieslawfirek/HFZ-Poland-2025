from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt


ROOT = Path.cwd()

DB_DIR = ROOT / "data" / "derived"
DB_PATTERN = "sportowe_talenty_2025_wiek_fitnessgram*.duckdb"
TABLE = "uczniowie2025_wiek_fitnessgram"

OUT = ROOT / "results" / "03_validate_20msrt"
OUT.mkdir(parents=True, exist_ok=True)

FILE_SUMMARY = OUT / "20mSRT_opis_wiek_plec.csv"
FILE_OVERALL = OUT / "20mSRT_walidacja_ogolem.csv"
FILE_FREQ = OUT / "20mSRT_czestosci_wynikow.csv"
FILE_HEAP = OUT / "20mSRT_skupienia_wynikow.csv"
FILE_DIFFS = OUT / "20mSRT_roznice_sasiednie_wieki.csv"
FILE_1415 = OUT / "20mSRT_porownanie_14_15_lat.csv"
FILE_CONTROL = OUT / "20mSRT_kontrola.txt"
FIG_MEDIAN = OUT / "Rycina_20mSRT_mediana_IQR_wiek_plec.png"
FIG_MEAN = OUT / "Rycina_20mSRT_srednia_SD_wiek_plec.png"


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def find_db() -> Path:
    existing = [p for p in DB_DIR.glob(DB_PATTERN) if p.is_file()]
    if not existing:
        raise FileNotFoundError(
            "Not found bazy z nowym wiekiem FitnessGram.\n"
            "Run first:\n"
            "python .\\src\\01_prepare_age.py"
        )

    # 01_prepare_age.py never overwrites earlier databases and creates
    # kolejne wersje (_v2, _v3, ...). Do walidacji wybieramy najnowszy
    # a new file regardless of version suffix.
    return max(existing, key=lambda p: p.stat().st_mtime)


def choose_column(columns, candidates, required=True):
    lookup = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lookup:
            return lookup[cand.lower()]
    if required:
        raise KeyError(
            "Required column not found. Searched for: "
            + ", ".join(candidates)
        )
    return None


def main():
    print("=" * 92)
    print("VALIDATION AND DESCRIPTION OF RAW 20mSRT RESULTS — 2025")
    print("Age: completed years on 30 April 2025")
    print("=" * 92)

    db_path = find_db()
    con = duckdb.connect(str(db_path), read_only=True)

    tables = con.execute("SHOW TABLES").df()["name"].astype(str).tolist()
    if TABLE not in tables:
        con.close()
        raise RuntimeError(
            f"Table not found: {TABLE}. Available objects: "
            + ", ".join(tables)
        )

    cols = (
        con.execute(f"DESCRIBE {qident(TABLE)}")
        .df()["column_name"]
        .astype(str)
        .tolist()
    )

    col_age = choose_column(cols, ["wiek_fitnessgram"])
    col_sex = choose_column(cols, ["plec", "sex", "sex"])
    col_beep = choose_column(cols, ["beep", "pacer", "20msrt"])
    col_status = choose_column(cols, ["status_wieku_fitnessgram"], required=False)

    status_filter = ""
    if col_status:
        status_filter = f"AND {qident(col_status)} = 'wiek_przypisany'"

    base_sql = f"""
        SELECT
            TRY_CAST({qident(col_age)} AS INTEGER) AS age,
            CAST({qident(col_sex)} AS VARCHAR) AS sex,
            TRY_CAST({qident(col_beep)} AS DOUBLE) AS beep
        FROM {qident(TABLE)}
        WHERE TRY_CAST({qident(col_age)} AS INTEGER) BETWEEN 10 AND 19
          AND CAST({qident(col_sex)} AS VARCHAR) IN ('dz', 'ch')
          AND TRY_CAST({qident(col_beep)} AS DOUBLE) IS NOT NULL
          {status_filter}
    """

    n_total = int(
        con.execute(f"SELECT COUNT(*) FROM ({base_sql})").fetchone()[0]
    )
    if n_total == 0:
        con.close()
        raise RuntimeError("No observations remain after filtering.")

    summary = con.execute(
        f"""
        WITH b AS ({base_sql})
        SELECT
            age AS wiek,
            sex AS plec,
            COUNT(*) AS n,
            AVG(beep) AS srednia,
            STDDEV_SAMP(beep) AS sd,
            MEDIAN(beep) AS mediana,
            QUANTILE_CONT(beep, 0.25) AS q1,
            QUANTILE_CONT(beep, 0.75) AS q3,
            QUANTILE_CONT(beep, 0.75) - QUANTILE_CONT(beep, 0.25) AS iqr,
            MIN(beep) AS minimum,
            MAX(beep) AS maksimum,
            SUM(CASE WHEN beep = 1 THEN 1 ELSE 0 END) AS n_eq_1,
            SUM(CASE WHEN beep = 180 THEN 1 ELSE 0 END) AS n_eq_180,
            SUM(CASE WHEN beep > 150 THEN 1 ELSE 0 END) AS n_gt_150
        FROM b
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).df()

    summary["pct_eq_1"] = (100 * summary["n_eq_1"] / summary["n"]).round(4)
    summary["pct_eq_180"] = (100 * summary["n_eq_180"] / summary["n"]).round(4)
    summary["pct_gt_150"] = (100 * summary["n_gt_150"] / summary["n"]).round(4)

    for c in ["srednia", "sd", "mediana", "q1", "q3", "iqr", "minimum", "maksimum"]:
        summary[c] = summary[c].round(3)

    summary.to_csv(FILE_SUMMARY, index=False, encoding="utf-8-sig")

    overall = con.execute(
        f"""
        WITH b AS ({base_sql})
        SELECT
            COUNT(*) AS n,
            AVG(beep) AS srednia,
            STDDEV_SAMP(beep) AS sd,
            MEDIAN(beep) AS mediana,
            QUANTILE_CONT(beep, 0.25) AS q1,
            QUANTILE_CONT(beep, 0.75) AS q3,
            MIN(beep) AS minimum,
            MAX(beep) AS maksimum,
            SUM(CASE WHEN beep < 0 THEN 1 ELSE 0 END) AS n_lt_0,
            SUM(CASE WHEN beep = 0 THEN 1 ELSE 0 END) AS n_eq_0,
            SUM(CASE WHEN beep = 1 THEN 1 ELSE 0 END) AS n_eq_1,
            SUM(CASE WHEN beep > 150 THEN 1 ELSE 0 END) AS n_gt_150,
            SUM(CASE WHEN beep = 180 THEN 1 ELSE 0 END) AS n_eq_180,
            SUM(CASE WHEN beep > 180 THEN 1 ELSE 0 END) AS n_gt_180,
            SUM(
                CASE WHEN ABS(beep - ROUND(beep)) > 0.000001
                THEN 1 ELSE 0 END
            ) AS n_niecalowite
        FROM b
        """
    ).df()

    for c in ["srednia", "sd", "mediana", "q1", "q3", "minimum", "maksimum"]:
        overall[c] = overall[c].round(3)

    for c in [
        "n_lt_0", "n_eq_0", "n_eq_1", "n_gt_150",
        "n_eq_180", "n_gt_180", "n_niecalowite"
    ]:
        overall["pct_" + c[2:]] = (
            100.0 * overall[c] / overall["n"]
        ).round(5)

    overall.to_csv(FILE_OVERALL, index=False, encoding="utf-8-sig")

    freq = con.execute(
        f"""
        WITH b AS ({base_sql})
        SELECT beep AS wynik_20mSRT, COUNT(*) AS n
        FROM b
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    freq["procent"] = (100.0 * freq["n"] / n_total).round(5)
    freq.to_csv(FILE_FREQ, index=False, encoding="utf-8-sig")

    heap = con.execute(
        f"""
        WITH b AS ({base_sql}),
        x AS (
            SELECT CAST(ROUND(beep) AS INTEGER) AS beep_int
            FROM b
            WHERE ABS(beep - ROUND(beep)) <= 0.000001
        )
        SELECT beep_int % 10 AS ostatnia_cyfra, COUNT(*) AS n
        FROM x
        GROUP BY 1
        ORDER BY 1
        """
    ).df()
    heap["procent"] = (100.0 * heap["n"] / heap["n"].sum()).round(4)
    heap.to_csv(FILE_HEAP, index=False, encoding="utf-8-sig")

    diffs_rows = []
    for sex in ["dz", "ch"]:
        s = summary[summary["plec"] == sex].sort_values("wiek")
        for age in range(10, 19):
            r1 = s[s["wiek"] == age]
            r2 = s[s["wiek"] == age + 1]
            if len(r1) == 1 and len(r2) == 1:
                diffs_rows.append(
                    {
                        "plec": sex,
                        "wiek_ml": age,
                        "wiek_st": age + 1,
                        "roznica_sredniej_starszy_minus_ml": round(
                            float(r2.iloc[0]["srednia"]) - float(r1.iloc[0]["srednia"]),
                            3,
                        ),
                        "roznica_mediany_starszy_minus_ml": round(
                            float(r2.iloc[0]["mediana"]) - float(r1.iloc[0]["mediana"]),
                            3,
                        ),
                    }
                )

    diffs = pd.DataFrame(diffs_rows)
    diffs.to_csv(FILE_DIFFS, index=False, encoding="utf-8-sig")

    comp_1415 = diffs[
        (diffs["wiek_ml"] == 14) & (diffs["wiek_st"] == 15)
    ].copy()
    comp_1415.to_csv(FILE_1415, index=False, encoding="utf-8-sig")

    # Figure 1: median + IQR
    fig, ax = plt.subplots(figsize=(8, 5.2))
    for sex, label in [("dz", "Girls"), ("ch", "Boys")]:
        s = summary[summary["plec"] == sex].sort_values("wiek")
        ax.plot(s["wiek"], s["mediana"], marker="o", label=label)
        ax.fill_between(
            s["wiek"].to_numpy(),
            s["q1"].to_numpy(dtype=float),
            s["q3"].to_numpy(dtype=float),
            alpha=0.15,
        )
    ax.set_xlabel("Age (years)")
    ax.set_ylabel("20mSRT completed shuttles")
    ax.set_xticks(range(10, 20))
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(FIG_MEDIAN, dpi=300, bbox_inches="tight")
    plt.close(fig)

    # Figure 2: mean ± SD
    fig, ax = plt.subplots(figsize=(8, 5.2))
    for sex, label in [("dz", "Girls"), ("ch", "Boys")]:
        s = summary[summary["plec"] == sex].sort_values("wiek")
        ax.errorbar(
            s["wiek"],
            s["srednia"],
            yerr=s["sd"],
            marker="o",
            capsize=3,
            label=label,
        )
    ax.set_xlabel("Age (years)")
    ax.set_ylabel("20mSRT completed shuttles, mean ± SD")
    ax.set_xticks(range(10, 20))
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(FIG_MEAN, dpi=300, bbox_inches="tight")
    plt.close(fig)

    con.close()

    lines = [
        "VALIDATION AND DESCRIPTION OF RAW 20mSRT RESULTS — 2025",
        "=" * 80,
        "",
        f"Database: {db_path}",
        f"Table: {TABLE}",
        "Age: completed years on 30 April 2025.",
        "Scope: ages 10–19, sex dz/ch, available 20mSRT result.",
        "HFZ status was not used to describe the raw 20mSRT distribution.",
        "",
        f"N = {n_total:,}",
        "",
        "OVERALL:",
        overall.to_string(index=False),
        "",
        "AGE 14 -> 15 COMPARISON:",
        comp_1415.to_string(index=False) if not comp_1415.empty else "Brak danych.",
        "",
        "NOTE:",
        "The 20mSRT result is treated as the number of completed 20-m shuttles.",
        "Values >150 and =180 are audit flags and are not automatically removed.",
        "Values >180, negative, zero, and non-integer values are reported separately.",
    ]
    FILE_CONTROL.write_text("\n".join(lines), encoding="utf-8")

    print("\nDONE")
    print(f"N = {n_total:,}")
    print("\nOverall:")
    print(overall.to_string(index=False))
    print("\n14 -> 15 lat:")
    print(comp_1415.to_string(index=False) if not comp_1415.empty else "Brak danych.")
    print("\nPliki zapisano w:")
    print(OUT)
    print("=" * 92)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nERROR:", exc)
        sys.exit(1)
