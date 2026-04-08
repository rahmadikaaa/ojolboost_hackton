# OjolBoost: Multi-Agent Management System (MAMS)

OjolBoost adalah sebuah **Multi-Agent Management System (MAMS)** berbasis *Artificial Intelligence* yang dirancang khusus untuk para pekerja *gig economy* (ojek online, kurir pengiriman barang, dan logistik) di Indonesia. Sistem ini berfungsi sebagai "Digital Copilot" terpadu untuk menyelesaikan masalah fragmentasi aplikasi dan kelelahan kognitif bagi para *driver* yang mengoperasikan beberapa platform secara bersamaan.

Sistem ini didukung oleh **Google Vertex AI (Gemini 2.5 Flash)** yang terintegrasi secara *native* dengan infrastruktur **Google Cloud (BigQuery, Cloud Run)**, serta menggunakan standar **Google Agent Development Kit (ADK)** dan arsitektur pengiriman pesan cerdas menggunakan **Model Context Protocol (MCP)**.

---

## 🚀 Fitur Utama & Keunggulan

OjolBoost menstruktur pengambilan keputusan dari berbasis intuisi ke *data-driven*, dengan optimalisasi tingkat konversi *Active-hour yield*. 

1. **Target Reverse Calculator**: *Driver* dapat menentukan target harian, dan sistem akan mengalkulasi berapa *rides/orders* yang dibutuhkan secara *real-time* berdasarkan *historical cost*.
2. **Predictive Heatmaps**: Memberikan saran area yang paling *untung* (bukan hanya area padat) dengan menganalisis probabilitas tinggi menggunakan data historis di **BigQuery**.
3. **Context-Aware Pumping**: Menganalisis kondisi eksternal seperti cuaca (*OpenWeather*) secara otomatis dan mengalihkan strategi (Misal: ganti layanan menjadi *Food Delivery* apabila sedang hujan lebat).
4. **Automated Planners & Reminders**: Mensinkronisasikan jadwal operasional, penggantian oli, maupun pencatatan *maintenance* ke **Google Calendar** secara otomatis.

---

## 🏗️ Arsitektur "Bang Jek" Ekosistem (Master-Subordinate)

Arsitektur aplikasi terbagi menjadi satu *Primary Orchestrator* bernama **"Bang Jek"** yang didukung oleh beberapa Sub-Agen spesialis.

| Nama Agen | Peran Teknis | Tanggung Jawab Utama | Akses / Tools |
| :--- | :--- | :--- | :--- |
| **Bang Jek** | *Primary Orchestrator* | Menerima input *user*, Intent Analysis, evaluasi dan mendelegasikan instruksi ke agen bawahan. Mengolah respon final *user*. | Sistem internal, tanpa eksekusi langsung |
| **Demand Analytics** | *Data Scientist* | Analisa tren lokasi dan probabilitas *demand* historis dari dataset perusahaan. | BigQuery (Read-Only) |
| **Environmental** | *Weather Monitor* | Memantau cuaca dan memberikan peringatan untuk penyesuaian strategi. | OpenWeather API |
| **The Planner** | *Operations Manager* | Manajemen jadwal pengguna secara langsung (Catat target, pengingat, dll). | MCP Calendar, MCP Tasks |
| **The Archivist** | *Knowledge Base* | Database memori personal. Menyimpan preferensi, riwayat dan catatan khusus pengguna. | MCP Google Notes / Keep |
| **The Auditor** | *Finance Auditor* | Mencatat dan menganalisa keuangan asimetris (*State Management* dan Pelaporan Data). | BigQuery (L3 Guardrails Write) |

### Mekanisme Kerja:
1. **Bang Jek** adalah SATU-SATUNYA agen yang berinteraksi dalam Natural Language kepada Pengguna.
2. Semua sub-agen mendelegasikan tugas dalam batasan **Guardrails L3** (*Deterministic hooks* untuk memitigasi eksekusi aksi di luar kendali).
3. Hasil akhir dikumpulkan sebagai data JSON *(Results Only)* dan Bang Jek (Orkestrator) akan merangkum hal tersebut menjadi penyampaian yang hangat (misal: dengan logat "Bang").

---

## ⚙️ Persyaratan Sistem (Prerequisites)

Untuk menjalankan OjolBoost PAMS secara lokal, Anda butuh:
1. **Python 3.10+**
2. **Google Cloud Account** (Konfigurasi Service Account dengan akses *Secret Manager*, *Vertex AI*, *BigQuery*).
3. Library terdaftar (lihat `requirements.txt`).

---

## 💻 Instalasi & Cara Penggunaan (Deployment Lokal)

**1. Clone Repo dan Masuk**
```bash
git clone <url-repo-ojolboost>
cd ojolboost
```

**2. Instalasi Dependensi (dianjurkan menggunakan Virtual Environment)**
```bash
python -m venv venv
source venv/Scripts/activate  # Untuk Windows (CMD/PowerShell)
pip intall -r requirements.txt
```

**3. Konfigurasi Lingkungan (`.env`)**
Buat file `.env` di dalam *root directory* dan isi konfigurasi berikut (pastikan file `.env` masuk dalam `.gitignore` sebagaimana dikonfigurasi):
```ini
GOOGLE_CLOUD_PROJECT=ojolboost-xxx # ID Project Anda
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
VERTEX_AI_LOCATION=asia-southeast2
OPENWEATHER_API_KEY=YOUR_API_KEY
MCP_SERVER_PORT=8080
```

**4. Menjalankan MCP Server OjolBoost**
Agar sub-agen dapat berinteraksi ke berbagai infrastruktur (Cloud/Tools), jalankan MCP server secara lokal di terminal pertama:
```bash
python run_local.py
```

**5. Menguji Eksekusi CLI Interactive / Simulator (Terminal Kedua)**
Sebagai UI Prototipe, Anda dapat berinteraksi langsung dengan "Bang Jek" via CLI/Terminal:
```bash
python chat.py
```
*Cobalah dengan perintah: "Bang Jek, catat hari ini dapet bersih 250 ribu, trus cek cuaca di Kemang jam 3 ntar gimana."*

---

## 📂 Struktur Direktori (*5 Layers ADK*)

Direktori sistem dirancang mematuhi **Arsitektur 5 Lapis ADK**:
```
c:\ojolboost\
├── agents/             # [Layer 4] Core Agen AI (Bang Jek & 5 Sub-Agen)
├── guardrails/         # [Layer 3] Keamanan & Deterministic Validator (e.g. auditor_validator)
├── skills/             # [Layer 2] Fragmentasi ilmu agen dalam markdown 
├── shared/             # [Layer 1] Memory, Schema Pydantic, Logging JSON, Session Context Layer
├── deploy/             # [Layer 5] Infrastruktur (Dockerfile, Cloud Run yaml)
├── mcp_server/         # Eksekusi protokol MCP untuk akses Tools Eksternal 
├── chat.py             # Interface Utama interaksi Terminal CLI
├── run_local.py        # Eksekutor instansiasi MCP Server Flask
├── tests/              # Validasi dan Integration Tests Unit
├── requirements.txt    # Library dependensi
└── CLAUDE.md           # [Layer 1] Aturan main arsitektur yang tidak dapat dilanggar
```

---

## 🛡️ Aturan Kontribusi & Standarisasi Guardrails

Untuk berkontribusi pada pengembangan arsitektur OjolBoost, pastikan mematuhi aturan berikut (dijelaskan detail di `CLAUDE.md`):
* Gunakan konvensi **Pydantic Schemas** pada seluruh interaksi modul antar agen (simpan schema dalam `shared/schemas.py`).
* Setiap agen memiliki L3 Interceptors `pre_tool_use.py` dan `post_tool_use.py`. **The Auditor WAJIB** menggunakan `auditor_validator.py` untuk menolak (`Hard Block`) aksi database *destructive* seperti DELETE / DROP tabel.
* File `CLAUDE.md` dan struktur ini merupakan *Truth of Source* Sistem Multi-Agen Bang Jek dan TIDAK BISA dinegosiasikan tanpa otorisasi owner.

---

> *OjolBoost: Elevating Gig Economy with Intelligent Orchestration.*
