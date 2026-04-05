# CLAUDE.md — Konstitusi OjolBoost MAMS
# Memory & Constitution Layer (ADK Layer 1)
# ============================================================
# DOKUMEN INI ADALAH SOURCE OF TRUTH YANG TIDAK DAPAT DINEGOSIASI.
# Setiap agen, tool, dan proses dalam sistem ini harus mematuhi
# aturan yang tercantum di bawah ini secara absolut.
# ============================================================

## 0. PROLOG: MISI & KONTEKS SISTEM

Sistem ini adalah **OjolBoost Multi-Agent Management System (MAMS)**.
Tujuannya adalah mentransformasi pengambilan keputusan pengemudi ojek online
dari berbasis intuisi menjadi orkestrasi berbasis data (data-driven),
dengan target metrik **Active-hour yield** yang teroptimasi.

Model Fondasi : Google Vertex AI — Gemini 2.5 Flash
Framework     : Google Agent Development Kit (ADK)
Protokol Alat : Model Context Protocol (MCP) — stateless & modular
Infrastruktur : Google Cloud Run (serverless, single runtime, native delegation)
Dataset Utama : BigQuery project `ojolboosttrack2`

---

## 1. ATURAN ABSOLUT ORKESTRATOR (TIDAK DAPAT DILANGGAR)

### 1.1 Bang Jek Adalah Satu-Satunya Primary Orchestrator

```
ATURAN #1 (IMMUTABLE):
"Bang Jek" adalah SATU-SATUNYA titik masuk (entry point) untuk semua
permintaan pengguna. Tidak ada sub-agen yang boleh menerima input
langsung dari pengguna atau dari agen lain selain Bang Jek.
```

- **Bang Jek hanya boleh MENDELEGASIKAN, tidak pernah mengeksekusi tool secara langsung.**
- Bang Jek tidak boleh memanggil BigQuery, OpenWeather, Calendar, atau MCP tool apapun secara langsung.
- Bang Jek melakukan Intent Analysis → menyusun Task Plan → mendelegasikan ke sub-agen yang tepat.
- Bang Jek menerima hasil (`results only`) dari sub-agen dan menyintesisnya menjadi narasi taktis untuk pengguna.

### 1.2 Hierarki Delegasi

```
Pengguna
   │
   ▼
Bang Jek (Primary Orchestrator) ──delegate──► Demand Analytics
                                 ──delegate──► Environmental
                                 ──delegate──► The Planner
                                 ──delegate──► The Archivist
                                 ──delegate──► The Auditor
```

- Delegasi bersifat **paralel** jika tugas independen.
- Delegasi bersifat **sekuensial** jika output satu agen dibutuhkan sebagai input agen lain.
- **Sub-agen tidak boleh saling mendelegasikan satu sama lain.**

### 1.3 Pola Komunikasi Wajib

| Pihak | Diizinkan | Dilarang |
|---|---|---|
| Bang Jek | Delegate ke sub-agen | Eksekusi tool langsung |
| Sub-agen | Eksekusi tools miliknya sendiri | Panggil agen lain, akses tool agen lain |
| Sub-agen | Return `results only` ke Bang Jek | Ambil keputusan akhir untuk pengguna |

---

## 2. ARSITEKTUR 5 LAPIS ADK (LAYER HIERARCHY)

```
┌─────────────────────────────────────────────────────────────┐
│  L5 — DEPLOYMENT & INFRA                                    │
│  deploy/ · Dockerfile · cloudbuild.yaml · service.yaml      │
├─────────────────────────────────────────────────────────────┤
│  L4 — AGENTS & DELEGATION                                   │
│  agents/bang_jek/ · agents/demand_analytics/                │
│  agents/environmental/ · agents/the_planner/                │
│  agents/the_archivist/ · agents/the_auditor/                │
├─────────────────────────────────────────────────────────────┤
│  L3 — GUARDRAILS (Deterministic Event Hooks)                │
│  guardrails/pre_tool_use.py · post_tool_use.py              │
│  guardrails/auditor_validator.py                            │
├─────────────────────────────────────────────────────────────┤
│  L2 — SKILLS (Fragmented Knowledge, Auto-invoked)           │
│  skills/<agent_name>/*.md                                   │
├─────────────────────────────────────────────────────────────┤
│  L1 — MEMORY & CONSTITUTION                                 │
│  CLAUDE.md · shared/schemas.py · shared/context.py          │
└─────────────────────────────────────────────────────────────┘
```

**Prinsip utama**: Lapisan bawah tidak boleh bergantung pada lapisan atas.
`shared/` dapat diakses dari semua lapisan. `guardrails/` harus diaktifkan
oleh `agents/` saat runtime.

---

## 3. DIREKTORI DAN TANGGUNG JAWAB MODUL

### 3.1 `agents/` — Layer 4

Setiap folder sub-agen WAJIB memiliki struktur:
```
agents/<nama_agen>/
├── __init__.py       ← Ekspor objek agent utama
├── agent.py          ← Definisi ADK Agent, system prompt diambil dari config.py
├── config.py         ← AGENT_NAME, SYSTEM_PROMPT, allowed tools, model
└── tools.py          ← Implementasi tool (wrapper API/MCP/BigQuery)
```

`the_auditor` memiliki tambahan:
```
agents/the_auditor/
└── validator.py      ← L3 integration: dipanggil SEBELUM tools.py dieksekusi
```

### 3.2 `skills/` — Layer 2

```
skills/<nama_agen>/
└── <nama_skill>.md
```

- Setiap file `.md` adalah **knowledge fragment** yang berdiri sendiri.
- File dipanggil secara **auto-invoked** berdasarkan description matching, bukan import Python.
- Format wajib setiap skill file:
  ```markdown
  # Skill: <Nama Skill>
  # Agent: <Nama Agen Pemilik>
  # Trigger: <Kapan skill ini di-invoke, dalam natural language>
  # Description: <Deskripsi singkat untuk matching>
  ---
  [Isi instruksi/knowledge]
  ```
- **Agen hanya boleh mengakses skill folder miliknya sendiri.**

### 3.3 `guardrails/` — Layer 3

- `base_hook.py`: Abstract class `BaseHook` dengan method `pre_tool_use()` dan `post_tool_use()`.
- `pre_tool_use.py`: Intercept **semua** pemanggilan tool. Wajib diregistrasi di `agent.py` masing-masing agen.
- `post_tool_use.py`: Validasi output tool. Log anomali ke `shared/logger.py`.
- `auditor_validator.py`: **Hard block khusus untuk The Auditor** — lihat Seksi 5.

### 3.4 `shared/` — Cross-layer Utilities

- `schemas.py`: Semua Pydantic model yang digunakan lintas agen. **Satu-satunya sumber kebenaran untuk tipe data.**
- `context.py`: Session context manager. Menyimpan state percakapan yang aman dan stateless per request.
- `logger.py`: Structured logging dengan format JSON. Wajib digunakan oleh semua komponen.

### 3.5 `mcp_server/` — MCP Integration

- `server.py` WAJIB direfactor agar:
  1. Semua request/response menggunakan Pydantic model dari `shared/schemas.py`.
  2. Setiap tool call yang masuk divalidasi oleh L3 Guardrails sebelum diproses.
  3. Tidak menyimpan state di level server — semua state dikelola oleh `shared/context.py`.

---

## 4. NAMING CONVENTIONS (WAJIB DIIKUTI)

### 4.1 File dan Direktori
| Konteks | Konvensi | Contoh |
|---|---|---|
| Direktori agen | `snake_case` | `demand_analytics/`, `the_auditor/` |
| File Python | `snake_case` | `agent.py`, `pre_tool_use.py` |
| File Skill | `snake_case` + deskriptif | `hotzone_identifier.md` |
| File Config/Deploy | `snake_case` | `service.yaml`, `cloudbuild.yaml` |

### 4.2 Kode Python
| Konteks | Konvensi | Contoh |
|---|---|---|
| Class | `PascalCase` | `DemandAnalyticsAgent`, `AuditorValidator` |
| Fungsi / Method | `snake_case` | `delegate_task()`, `validate_query()` |
| Konstanta | `UPPER_SNAKE_CASE` | `BIGQUERY_DATASET`, `MAX_LATENCY_MS` |
| Variabel | `snake_case` | `task_result`, `weather_data` |
| Pydantic Model | `PascalCase` + suffix `Schema` | `TransactionSchema`, `WeatherResponseSchema` |
| ADK Agent var | `snake_case` + suffix `_agent` | `bang_jek_agent`, `auditor_agent` |

### 4.3 Naming Agen dalam System Prompt
Nama agen dalam `config.py` harus persis:
- `"Bang Jek"` — bukan "BangJek", "bang_jek", atau "Orchestrator"
- `"Demand Analytics"` — bukan "DemandAnalytics"
- `"Environmental"` — bukan "WeatherAgent"
- `"The Planner"` — bukan "Planner"
- `"The Archivist"` — bukan "Archivist"
- `"The Auditor"` — bukan "Auditor" atau "FinanceAgent"

### 4.4 BigQuery Naming
- Dataset: `ojolboosttrack2` (immutable, sesuai PRD)
- Tabel: `snake_case`, prefix sesuai domain: `trx_`, `zone_`, `schedule_`
- Contoh: `trx_daily_income`, `zone_demand_history`, `schedule_reminders`

---

## 5. GUARDRAILS UNTUK THE AUDITOR (L3 — HARD BLOCK)

### 5.1 Prinsip Dasar

```
ATURAN #2 (IMMUTABLE):
The Auditor TIDAK BOLEH mengeksekusi query BigQuery apapun tanpa
melalui validasi deterministik dari auditor_validator.py terlebih dahulu.
Bypass terhadap validator adalah pelanggaran arsitektur kritis.
```

### 5.2 Alur Validasi Wajib

```
Bang Jek
   │ delegate(task="catat transaksi ...")
   ▼
The Auditor (agent.py)
   │
   ├──► auditor_validator.py  ◄─── guardrails/auditor_validator.py
   │         │
   │    [VALIDATION CHECKLIST]
   │    ✓ 1. Schema Check: payload sesuai TransactionSchema (shared/schemas.py)?
   │    ✓ 2. Operation Check: hanya INSERT/SELECT yang diizinkan (no DELETE/DROP)?
   │    ✓ 3. Dataset Check: target dataset == "ojolboosttrack2"?
   │    ✓ 4. Table Whitelist: nama tabel ada di whitelist yang terdefinisi?
   │    ✓ 5. Field Completeness: semua required fields terisi?
   │         │
   │    [PASS] ──────────────────► tools.py (eksekusi query BigQuery)
   │    [FAIL] ──────────────────► raise AuditorValidationError (log + return error ke Bang Jek)
   │
   └──► return result ke Bang Jek
```

### 5.3 Operasi SQL yang Diizinkan & Dilarang

| Operasi | Status | Keterangan |
|---|---|---|
| `SELECT` | ✅ DIIZINKAN | Untuk analisis dan reporting |
| `INSERT` | ✅ DIIZINKAN | Untuk pencatatan transaksi baru |
| `UPDATE` | ⚠️ TERBATAS | Hanya dengan kolom `updated_at`, field finansial tidak boleh di-UPDATE |
| `DELETE` | ❌ DILARANG | Tidak ada penghapusan data finansial |
| `DROP` | ❌ DILARANG KERAS | Pelanggaran kritis, trigger alert |
| `TRUNCATE` | ❌ DILARANG KERAS | Pelanggaran kritis, trigger alert |
| `CREATE TABLE` | ❌ DILARANG | Hanya DBA (via script deploy) yang boleh membuat tabel |
| `ALTER TABLE` | ❌ DILARANG | Hanya DBA (via script deploy) yang boleh mengubah schema |

---

## 6. SPESIFIKASI SUB-AGEN

### 6.1 Demand Analytics
- **Peran**: Data Scientist
- **Tools diizinkan**: BigQuery (read-only, dataset `ojolboosttrack2`)
- **Tools dilarang**: OpenWeather, Calendar, MCP Notes, BigQuery WRITE
- **Skills**: `hotzone_identifier`, `historical_trend_query`, `opportunity_cost_calc`
- **Output format**: JSON dengan field `zones[]`, `probability_score`, `recommendation`

### 6.2 Environmental
- **Peran**: Weather Monitor
- **Tools diizinkan**: OpenWeather API
- **Tools dilarang**: BigQuery, Calendar, MCP Notes
- **Skills**: `weather_alert_rules`, `service_pivot_logic`, `api_response_parser`
- **Output format**: JSON dengan field `condition`, `alert_level`, `pivot_recommendation`

### 6.3 The Planner
- **Peran**: Operations Manager
- **Tools diizinkan**: MCP Calendar, MCP Task Manager
- **Tools dilarang**: BigQuery, OpenWeather, MCP Notes
- **Skills**: `schedule_creation`, `task_reminder_format`, `conflict_resolution`
- **Output format**: JSON dengan field `event_id`, `status`, `scheduled_at`

### 6.4 The Archivist
- **Peran**: Knowledge Base
- **Tools diizinkan**: MCP Google Notes / Keep
- **Tools dilarang**: BigQuery, OpenWeather, Calendar
- **Skills**: `note_indexing`, `semantic_search`, `keep_sync_protocol`
- **Output format**: JSON dengan field `note_id`, `title`, `content`, `tags[]`

### 6.5 The Auditor
- **Peran**: Finance Auditor & State Manager
- **Tools diizinkan**: BigQuery (INSERT + SELECT, dataset `ojolboosttrack2` only)
- **Tools dilarang**: OpenWeather, Calendar, MCP Notes, BigQuery pada dataset lain
- **Guardrail**: `auditor_validator.py` wajib dijalankan sebelum semua operasi BigQuery
- **Skills**: `transaction_schema`, `sql_write_rules`, `financial_report_format`, `state_management`
- **Output format**: JSON dengan field `transaction_id`, `status`, `balance_snapshot`

---

## 7. EKSPEKTASI TESTING

### 7.1 Struktur Test Wajib

```
tests/
├── unit/
│   ├── test_bang_jek_router.py        ← Intent analysis & task decomposition
│   ├── test_auditor_validator.py      ← Semua kasus validasi L3
│   └── test_guardrails.py             ← PreToolUse & PostToolUse hooks
└── integration/
    └── test_atomic_multitask.py       ← Skenario end-to-end dari PRD
```

### 7.2 Coverage Minimum

| Komponen | Coverage Minimum |
|---|---|
| `auditor_validator.py` | **100%** — setiap branch validasi harus ter-cover |
| `bang_jek/router.py` | **95%** — semua intent pattern yang diketahui |
| `guardrails/` (semua file) | **90%** |
| `agents/<sub-agen>/tools.py` | **80%** (mock BigQuery & API yang diizinkan) |

### 7.3 Standar Test untuk Auditor Validator

Setiap test `test_auditor_validator.py` WAJIB mencakup:
- ✅ `test_valid_insert_transaction()` — happy path
- ✅ `test_valid_select_report()` — happy path
- ❌ `test_blocked_delete_query()` — HARUS raise `AuditorValidationError`
- ❌ `test_blocked_drop_table()` — HARUS raise `AuditorValidationError`
- ❌ `test_wrong_dataset_target()` — HARUS raise `AuditorValidationError`
- ❌ `test_missing_required_fields()` — HARUS raise `AuditorValidationError`
- ❌ `test_unknown_table_name()` — HARUS raise `AuditorValidationError`

### 7.4 Standar Test Integrasi

Skenario wajib dari PRD:
```
Input: "Bang Jek, catat pendapatan 250 ribu hari ini, cek cuaca Sudirman,
        dan ingetin besok jam 9 pagi buat ganti oli."

Expected:
- The Auditor dipanggil → INSERT ke trx_daily_income → status: success
- Environmental dipanggil → OpenWeather Sudirman → kondisi cuaca ter-return
- The Planner dipanggil → Calendar entry created → event_id ter-return
- Bang Jek menyintesis 3 hasil → narasi taktis dalam Bahasa Indonesia

Constraint:
- Total latency < 5000ms
- Reasoning Accuracy (agen yang dipilih) = 100% untuk kasus ini
- Tidak ada intervensi manual yang dibutuhkan
```

---

## 8. ATURAN PENGGUNAAN BAHASA & PERSONA

### 8.1 Bahasa Output Bang Jek
- **Selalu dalam Bahasa Indonesia** dengan nada yang hangat, lugas, dan seperti teman.
- Gunakan sapaan **"Bang"** untuk menyapa pengguna.
- Instruksi taktis harus spesifik, bukan generik.
  - ✅ `"Bang, Sudirman mau hujan. Data nunjukin Food lagi naik, mending geser ke sana!"`
  - ❌ `"Cuaca di area tersebut diprediksi akan memburuk."`

### 8.2 Bahasa Internal Sub-agen
- Output sub-agen (yang dikembalikan ke Bang Jek) harus dalam format **JSON yang bersih dan terstruktur**.
- Sub-agen tidak berkomunikasi dalam natural language — hanya data terstruktur.
- Narasi natural language adalah **eksklusif tanggung jawab Bang Jek**.

---

## 9. ATURAN DEPLOYMENT & ENVIRONMENT

### 9.1 Variabel Lingkungan Wajib (`.env`)
```
# Google Cloud
GOOGLE_CLOUD_PROJECT=ojolboosttrack2
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
VERTEX_AI_LOCATION=asia-southeast1

# APIs
OPENWEATHER_API_KEY=<secret>

# MCP
MCP_SERVER_HOST=localhost
MCP_SERVER_PORT=8080

# Runtime
MAX_AGENT_LATENCY_MS=5000
BIGQUERY_DATASET=ojolboosttrack2
```

### 9.2 Aturan Cloud Run
- Semua agen berjalan dalam **satu container, satu runtime Python** (native ADK delegation).
- **Dilarang** memecah sub-agen ke dalam Cloud Run service yang terpisah.
- IAM: Gunakan **Service Account khusus dengan prinsip least privilege** — akses BigQuery hanya untuk dataset `ojolboosttrack2`.

### 9.3 Secrets Management
- **Dilarang** melakukan hardcode API key atau credential di dalam kode sumber.
- Gunakan **Google Secret Manager** untuk semua secret di environment produksi.
- File `.env` hanya untuk pengembangan lokal dan wajib masuk `.gitignore`.

---

## 10. GLOSSARY & REFERENSI

| Istilah | Definisi |
|---|---|
| **Active-hour yield** | Pendapatan bersih per jam aktif berkendara |
| **Opportunity Cost of Idle Time** | Biaya peluang dari waktu menganggur tanpa orderan |
| **Atomic Multi-Tasking** | Eksekusi paralel tugas-tugas independen oleh beberapa sub-agen sekaligus |
| **Native Delegation** | Delegasi agen dalam satu runtime Python, tanpa HTTP call antar-service |
| **Results Only** | Pola di mana sub-agen hanya mengembalikan data terstruktur, bukan mengambil keputusan |
| **Hard Block** | Mekanisme guardrail yang menghentikan eksekusi dan mengembalikan error (tidak hanya log) |
| `ojolboosttrack2` | BigQuery project/dataset utama sistem ini (immutable) |

---

*Dokumen ini adalah versi hidup (living document). Perubahan terhadap aturan di Seksi 1, 2, dan 5 memerlukan persetujuan eksplisit dari owner sistem dan harus didokumentasikan dengan tanggal perubahan.*

*Terakhir diperbarui: 2026-04-05*
