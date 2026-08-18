from __future__ import annotations
import sys
from pathlib import Path
import duckdb
import pandas as pd

ROOT = Path.cwd()

RAW_CANDIDATES = [
    ROOT / "data" / "raw" / "SportoweTalenty2025.csv",
]

OUT_DIR = ROOT / "results" / "09_repeated_records"
OUT_DIR.mkdir(parents=True, exist_ok=True)

F_SUMMARY = OUT_DIR / "wielokrotne_rekordy_20mSRT_2025_podsumowanie.csv"
F_DISTRIBUTION = OUT_DIR / "wielokrotne_rekordy_20mSRT_2025_rozklad.csv"
F_DETAILS = OUT_DIR / "wielokrotne_rekordy_20mSRT_2025_uczniowie_wielokrotni.csv"
F_CONTROL = OUT_DIR / "wielokrotne_rekordy_20mSRT_2025_kontrola.txt"

def find_raw():
    found = [p for p in RAW_CANDIDATES if p.exists()]
    if not found:
        raise FileNotFoundError("Nie znaleziono SportoweTalenty2025.csv.\nSprawdzono:\n" + "\n".join(map(str, RAW_CANDIDATES)))
    for p in found:
        if ROOT in p.parents:
            return p
    return found[0]

def pct(n, d):
    return 100.0*n/d if d else float("nan")

def cat(x):
    return "5+" if x >= 5 else str(int(x))

def main():
    print("="*90)
    print("WIELOKROTNE REKORDY 20mSRT — SPORTOWE TALENTY 2025")
    print("="*90)

    raw_path = find_raw()
    print("Plik źródłowy:", raw_path)

    con = duckdb.connect()
    raw_sql = f"""read_csv('{raw_path.as_posix()}', delim=';', header=true, all_varchar=true, ignore_errors=false)"""

    cols = con.execute(f"DESCRIBE SELECT * FROM {raw_sql}").df()["column_name"].astype(str).tolist()
    for c in ["student_id","proba","wynik","data_rejestracji","form_id"]:
        if c not in cols:
            raise RuntimeError(f"Brak kolumny {c}. Dostępne: {cols}")

    con.execute(f"""
        CREATE TEMP VIEW beep_raw AS
        SELECT
            TRIM(CAST(student_id AS VARCHAR)) AS student_id,
            TRIM(CAST(form_id AS VARCHAR)) AS form_id,
            TRIM(CAST(wynik AS VARCHAR)) AS wynik_raw,
            TRY_CAST(REPLACE(TRIM(CAST(wynik AS VARCHAR)), ',', '.') AS DOUBLE) AS wynik_num,
            TRIM(CAST(data_rejestracji AS VARCHAR)) AS data_rejestracji
        FROM {raw_sql}
        WHERE student_id IS NOT NULL
          AND TRIM(CAST(student_id AS VARCHAR)) <> ''
          AND LOWER(TRIM(CAST(proba AS VARCHAR))) = 'beep'
    """)

    counts = con.execute("""
        SELECT
            student_id,
            COUNT(*)::BIGINT AS n_beep_records,
            SUM(CASE WHEN wynik_num IS NOT NULL THEN 1 ELSE 0 END)::BIGINT AS n_valid_beep_records,
            MIN(data_rejestracji) AS first_registration_raw,
            MAX(data_rejestracji) AS last_registration_raw
        FROM beep_raw
        GROUP BY student_id
    """).df()

    n_rows = int(con.execute("SELECT COUNT(*) FROM beep_raw").fetchone()[0])
    n_students = len(counts)
    n_multi = int((counts["n_beep_records"] > 1).sum())
    p_multi = pct(n_multi, n_students)
    max_records = int(counts["n_beep_records"].max())

    valid = counts[counts["n_valid_beep_records"] > 0].copy()
    n_valid_rows = int(con.execute("SELECT COUNT(*) FROM beep_raw WHERE wynik_num IS NOT NULL").fetchone()[0])
    n_valid_students = len(valid)
    n_multi_valid = int((valid["n_valid_beep_records"] > 1).sum())
    p_multi_valid = pct(n_multi_valid, n_valid_students)

    dist_all = counts.assign(liczba_rekordow=counts["n_beep_records"].map(cat)).groupby("liczba_rekordow").size().reset_index(name="n_uczniow")
    dist_all["wariant"] = "Wszystkie rekordy Beep"
    dist_all["procent"] = 100*dist_all["n_uczniow"]/n_students

    dist_valid = valid.assign(liczba_rekordow=valid["n_valid_beep_records"].map(cat)).groupby("liczba_rekordow").size().reset_index(name="n_uczniow")
    dist_valid["wariant"] = "Tylko rekordy z liczbowym wynikiem"
    dist_valid["procent"] = 100*dist_valid["n_uczniow"]/n_valid_students

    distribution = pd.concat([dist_all, dist_valid], ignore_index=True)[["wariant","liczba_rekordow","n_uczniow","procent"]]

    summary = pd.DataFrame([
        ["Wszystkie rekordy Beep", n_rows, n_students, n_multi, p_multi, max_records],
        ["Tylko rekordy z liczbowym wynikiem", n_valid_rows, n_valid_students, n_multi_valid, p_multi_valid, int(valid["n_valid_beep_records"].max())],
    ], columns=["wariant","n_rekordow","n_uczniow_z_min_1_rekordem","n_uczniow_z_>1_rekordem","pct_uczniow_z_>1_rekordem","max_rekordow_na_ucznia"])

    details = counts[counts["n_beep_records"] > 1].sort_values(["n_beep_records","student_id"], ascending=[False,True])

    summary.to_csv(F_SUMMARY, index=False, encoding="utf-8-sig")
    distribution.to_csv(F_DISTRIBUTION, index=False, encoding="utf-8-sig")
    details.to_csv(F_DETAILS, index=False, encoding="utf-8-sig")

    sentence = (
        f"Więcej niż jeden rekord 20mSRT stwierdzono u {n_multi:,} z {n_students:,} "
        f"uczniów z co najmniej jednym zapisem testu ({p_multi:.2f}%)."
    ).replace(",", " ")

    if p_multi < 1:
        interp = "Odsetek jest bardzo mały; najprawdopodobniej wystarczy podać n i % bez analizy first vs latest."
    elif p_multi < 5:
        interp = "Odsetek jest niewielki; można podać n, % i rozkład liczby zapisów."
    else:
        interp = "Odsetek nie jest mały; warto rozważyć sensitivity analysis first vs latest."

    report = "\n".join([
        "WIELOKROTNE REKORDY 20mSRT — SPORTOWE TALENTY 2025",
        "="*90,
        f"Plik: {raw_path}",
        "",
        f"Liczba rekordów Beep: {n_rows:,}",
        f"Liczba uczniów z >=1 rekordem Beep: {n_students:,}",
        f"Liczba uczniów z >1 rekordem Beep: {n_multi:,}",
        f"Odsetek uczniów z >1 rekordem Beep: {p_multi:.4f}%",
        f"Maksymalna liczba rekordów u jednego ucznia: {max_records}",
        "",
        f"Liczba rekordów Beep z liczbowym wynikiem: {n_valid_rows:,}",
        f"Liczba uczniów z >=1 liczbowym wynikiem: {n_valid_students:,}",
        f"Liczba uczniów z >1 liczbowym wynikiem: {n_multi_valid:,}",
        f"Odsetek uczniów z >1 liczbowym wynikiem: {p_multi_valid:.4f}%",
        "",
        "SUGEROWANE ZDANIE DO MANUSKRYPTU:",
        sentence,
        "",
        "INTERPRETACJA:",
        interp,
    ])
    F_CONTROL.write_text(report, encoding="utf-8")

    con.close()

    print("\nGOTOWE")
    print(sentence)
    print(interp)
    print("Raport:", F_CONTROL)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\nBŁĄD:", e)
        sys.exit(1)
