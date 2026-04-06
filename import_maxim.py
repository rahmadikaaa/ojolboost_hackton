"""
Script Ingesti Data Massal - OjolBoost MAMS
File: import_maxim.py

Bertugas membaca raw_order.txt (format TSV), membuang data yang dibatalkan,
dan merekapitulasi total pendapatan per hari (untuk The Auditor) 
serta mengekstrak data rute (untuk Demand Analytics).
"""

import sys
import os
import re
import io
from collections import defaultdict
from typing import Dict, List, Tuple

# Fix for Windows Terminal UnicodeEncodeError
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def parse_biaya(biaya_str: str) -> int:
    """
    Membersihkan string angka dengan menghapus karakter non-digit ('Rp', spasi, dll)
    dan mengonversinya menjadi integer.
    """
    cleaned = re.sub(r'[^\d]', '', biaya_str)
    if not cleaned:
        return 0
    return int(cleaned)

def extract_origin_destination(rute_str: str) -> Tuple[str, str]:
    """
    Memisahkan string Rute menjadi origin (titik jemput) dan destination (titik antar)
    menggunakan separator em-dash yang lazim ada di histori Maxim.
    """
    # Gunakan split dengan karakter "—" (em-dash) atau "-" (dash standar)
    if " — " in rute_str:
        parts = rute_str.split(" — ", 1)
    elif " - " in rute_str:
        parts = rute_str.split(" - ", 1)
    else:
        # Fallback jika separator tidak ditemukan
        return rute_str.strip(), "Tidak diketahui"
    
    return parts[0].strip(), parts[1].strip()

def process_maxim_data(file_path: str):
    """
    Fungsi utama untuk mem-parsing file TSV Maxim, mem-filter status, 
    dan mengumpulkan metrik untuk The Auditor & Demand Analytics.
    """
    if not os.path.exists(file_path):
        print(f"❌ Error: File {file_path} tidak ditemukan.")
        sys.exit(1)

    total_berhasil = 0
    # Dictionary penyimpan total pendapatan harian: { 'Tanggal': total_rp }
    daily_income: Dict[str, int] = defaultdict(int)
    
    # List sampel rute untuk bukti ekstraksi ({origin, destination, distance})
    route_samples: List[Dict[str, str]] = []

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
            for line_num, line in enumerate(lines, start=1):
                raw_line = line.strip()
                if not raw_line or raw_line.startswith('#'):
                    # Lewati baris kosong atau baris header
                    continue
                
                # Pemisahan berdasarkan Tab (TSV)
                columns = raw_line.split('\t')
                
                # Format yang diharapkan minimal 9 kolom:
                # 0:#, 1:Tanggal, 2:Cabang, 3:Rute, 4:Jarak, 5:Biaya, 6:Tarif, 7:Jenis, 8:Status
                if len(columns) < 9:
                    continue
                
                tanggal = columns[1].strip()
                rute = columns[3].strip()
                jarak = columns[4].strip()
                biaya_str = columns[5].strip()
                status = columns[8].strip()

                # Filter HANYA yang dieksekusi (selesai)
                if status.lower() != "telah dieksekusi":
                    continue
                
                # --- The Auditor: Hitung Finansial ---
                biaya_int = parse_biaya(biaya_str)
                daily_income[tanggal] += biaya_int
                total_berhasil += 1
                
                # --- Demand Analytics: Kumpulkan sampel rute ---
                if len(route_samples) < 5:
                    origin, dest = extract_origin_destination(rute)
                    route_samples.append({
                        "tanggal": tanggal,
                        "origin": origin,
                        "destination": dest,
                        "jarak": jarak
                    })

        # =========================================
        # OUTPUT KE TERMINAL (MOCK MODE)
        # =========================================
        print("="*60)
        print("🚀 LAPORAN BATCH INGESTION (MOCK MAMS) 🚀")
        print("="*60)
        print(f"Total orderan berhasil diproses: {total_berhasil} trip\n")

        # Sortir tanggal ascending untuk laporan keuangan
        print("💰 [THE AUDITOR MOCK] REKAP PENDAPATAN HARIAN:")
        print("-" * 45)
        for tgl in sorted(daily_income.keys(), reverse=False):
            print(f"📅 {tgl}  : Rp {daily_income[tgl]:,}".replace(',', '.'))
        
        print("\n📍 [DEMAND ANALYTICS MOCK] SAMPEL EKSTRAKSI RUTE:")
        print("-" * 60)
        for i, sample in enumerate(route_samples, 1):
            print(f"Sample {i}:")
            print(f"   Waktu  : {sample['tanggal']}")
            print(f"   Titik A: {sample['origin']}")
            print(f"   Titik B: {sample['destination']}")
            print(f"   Jarak  : {sample['jarak']}")
            print()

        print("="*60)
        print("✅ Proses selesai dengan sukses.")

    except Exception as e:
        print(f"❌ Error saat memproses file: {str(e)}")

if __name__ == "__main__":
    # Menjalankan fungsi dengan path hardcode: raw_order.txt di direktori root
    process_maxim_data("raw_order.txt")
