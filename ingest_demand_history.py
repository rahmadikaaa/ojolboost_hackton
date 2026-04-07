"""
ingest_demand_history.py
========================
Ingest updated_raw.csv → BigQuery ojolboosttrack2.demand_history
Region: asia-southeast2

Cara pakai:
    python ingest_demand_history.py

Schema target:
    zone_name   STRING  ← Ekstrak dari teks dalam kurung pertama di kolom Rute
    tanggal     STRING  ← Kolom Tanggal apa adanya
    cabang      STRING  ← Kolom Cabang apa adanya
    rute        STRING  ← Kolom Rute apa adanya
    jarak       STRING  ← Kolom Jarak apa adanya
    biaya       STRING  ← Kolom Biaya apa adanya
    tarif       STRING  ← Kolom Tarif apa adanya
    jenis       STRING  ← Kolom Jenis apa adanya
    status      STRING  ← Kolom Status apa adanya
"""

import csv
import os
import re
import sys

from google.cloud import bigquery

# ── Config ──────────────────────────────────────────────────────────────────
PROJECT_ID  = os.getenv("GOOGLE_CLOUD_PROJECT", "ojolboost-487004")
DATASET     = "ojolboosttrack2"
TABLE       = "demand_history"
LOCATION    = "asia-southeast2"
CSV_FILE    = os.path.join(os.path.dirname(__file__), "updated_raw.csv")


def extract_zone_name(rute: str) -> str:
    """
    Ekstrak teks di dalam kurung PERTAMA dari string Rute.
    Contoh:
        "Warung X Jalan A (Pesanggrahan) ? Toko Y Jalan B (Rengas)"
        → "Pesanggrahan"
    Jika tidak ada kurung, kembalikan string kosong.
    """
    match = re.search(r'\(([^)]+)\)', rute)
    if match:
        return match.group(1).strip()
    return ""


def load_csv(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        for line_no, raw in enumerate(reader, start=1):
            # Lewati header
            if line_no == 1:
                continue
            # Lewati baris kosong atau tidak lengkap
            if not raw or len(raw) < 9:
                continue
            # Kolom: #;Tanggal;Cabang;Rute;Jarak;Biaya;Tarif;Jenis;Status
            _, tanggal, cabang, rute, jarak, biaya, tarif, jenis, status = (
                raw[0], raw[1], raw[2], raw[3], raw[4], raw[5], raw[6], raw[7], raw[8]
            )
            zone_name = extract_zone_name(rute)
            rows.append({
                "zone_name": zone_name,
                "tanggal":   tanggal.strip(),
                "cabang":    cabang.strip(),
                "rute":      rute.strip(),
                "jarak":     jarak.strip(),
                "biaya":     biaya.strip(),
                "tarif":     tarif.strip(),
                "jenis":     jenis.strip(),
                "status":    status.strip(),
            })
    return rows


def main():
    print(f"[INFO] Membaca {CSV_FILE} ...")
    rows = load_csv(CSV_FILE)
    print(f"   OK: {len(rows)} baris siap diingest.\n")

    if not rows:
        print("[ERROR] Tidak ada data. Batalkan.")
        sys.exit(1)

    # Preview 3 baris pertama
    print("Preview 3 baris pertama:")
    for r in rows[:3]:
        print(f"  zone_name={r['zone_name']!r:20s}  tanggal={r['tanggal']!r}  biaya={r['biaya']!r}")
    print()

    # Inisialisasi BigQuery client
    print(f"[INFO] Menghubungkan ke BigQuery project={PROJECT_ID}, location={LOCATION} ...")
    client = bigquery.Client(project=PROJECT_ID, location=LOCATION)

    table_ref = f"{PROJECT_ID}.{DATASET}.{TABLE}"

    # Pastikan tabel ada - jika belum ada, buat otomatis
    schema = [
        bigquery.SchemaField("zone_name", "STRING"),
        bigquery.SchemaField("tanggal",   "STRING"),
        bigquery.SchemaField("cabang",    "STRING"),
        bigquery.SchemaField("rute",      "STRING"),
        bigquery.SchemaField("jarak",     "STRING"),
        bigquery.SchemaField("biaya",     "STRING"),
        bigquery.SchemaField("tarif",     "STRING"),
        bigquery.SchemaField("jenis",     "STRING"),
        bigquery.SchemaField("status",    "STRING"),
    ]

    try:
        client.get_table(table_ref)
        print(f"   WARN: Tabel lama ditemukan -- menghapus dan membuat ulang ...")
        client.delete_table(table_ref)
        print(f"   OK: Tabel lama dihapus.")
    except Exception:
        print(f"   INFO: Tabel belum ada sebelumnya.")

    table_obj = bigquery.Table(table_ref, schema=schema)
    client.create_table(table_obj)
    print(f"   OK: Tabel {table_ref} berhasil dibuat dengan skema baru.\n")

    # Insert rows
    print(f"[INFO] Mengingest {len(rows)} baris ke {table_ref} ...")
    errors = client.insert_rows_json(table_ref, rows)

    if errors:
        print(f"\n[ERROR] Ada error saat insert:")
        for e in errors[:10]:
            print(f"   {e}")
        sys.exit(1)
    else:
        print(f"\n[SUKSES] {len(rows)} baris berhasil dimasukkan ke {table_ref}.")
        print(f"   Verifikasi: buka BigQuery Console -> {DATASET}.{TABLE}")


if __name__ == "__main__":
    main()
