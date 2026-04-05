"""
agents/bang_jek/router.py
=========================
Layer 4 — Intent Analyzer & Task Planner untuk Bang Jek.

Tanggung jawab:
- IntentAnalyzer : Mendeteksi sub-agen mana yang dibutuhkan dari input pengguna.
- TaskPlanner    : Menyusun rencana eksekusi (paralel/sekuensial) dari hasil analisis.

ATURAN (CLAUDE.md Seksi 1.3 & SYSTEM_PROMPT):
- Routing WAJIB berbasis logika deterministik (keyword rules), bukan probabilistik AI.
- Semua tipe data mengacu ke shared/schemas.py.
- Output TaskPlanner harus merupakan list AgentDelegation yang valid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Tuple

from shared.schemas import AgentDelegation
from shared.logger import get_logger

logger = get_logger("bang_jek.router")


# ============================================================
# ENUMS & DATA CLASSES
# ============================================================

class ExecutionMode(str, Enum):
    """Modus eksekusi tugas dalam task plan."""
    PARALLEL = "parallel"      # Tugas-tugas independen — dijalankan bersamaan
    SEQUENTIAL = "sequential"  # Tugas bergantung satu sama lain — dijalankan berurutan


class IntentType(str, Enum):
    """Tipe intent yang terdeteksi dari input pengguna."""
    RECORD_TRANSACTION = "record_transaction"
    CHECK_WEATHER = "check_weather"
    CREATE_SCHEDULE = "create_schedule"
    SAVE_NOTE = "save_note"
    SEARCH_NOTE = "search_note"
    ANALYZE_DEMAND = "analyze_demand"
    GET_FINANCIAL_REPORT = "get_financial_report"
    GET_DAILY_STATE = "get_daily_state"
    UNKNOWN = "unknown"


@dataclass
class DetectedIntent:
    """Hasil deteksi satu intent dari input pengguna."""
    intent_type: IntentType
    target_agent: str
    extracted_context: dict = field(default_factory=dict)
    raw_excerpt: str = ""       # Bagian kalimat yang memicu intent ini
    confidence: str = "high"    # "high" | "medium" | "low" — berdasarkan jumlah keyword match


@dataclass
class TaskPlan:
    """
    Rencana eksekusi yang dihasilkan TaskPlanner.
    Berisi daftar AgentDelegation yang siap dieksekusi oleh BangJekOrchestrator.
    """
    delegations: List[AgentDelegation]
    execution_mode: ExecutionMode
    total_tasks: int
    plan_summary: str = ""      # Ringkasan plan untuk logging


# ============================================================
# KEYWORD RULES — DETERMINISTIK
# Tidak menggunakan probabilitas. Setiap kata kunci dipetakan
# secara eksplisit ke sub-agen yang bertanggung jawab.
# ============================================================

# Format: { "agent_name": { intent_type: [keyword_list] } }
ROUTING_RULES: dict[str, dict[IntentType, list[str]]] = {

    "The Auditor": {
        IntentType.RECORD_TRANSACTION: [
            "catat pendapatan", "catat pemasukan", "catat uang",
            "masuk uang", "dapet uang", "dapat uang",
            "income", "pendapatan", "pemasukan", "penghasilan",
            "trip bayar", "orderan bayar", "dibayar",
            "catat transaksi", "input transaksi",
        ],
        IntentType.GET_FINANCIAL_REPORT: [
            "rekap", "laporan", "summary", "ringkasan keuangan",
            "total hari ini", "berapa hari ini", "berapa tadi",
            "saldo", "udah berapa", "sudah berapa", "hasil hari ini",
            "laporan harian", "laporan keuangan",
        ],
        IntentType.GET_DAILY_STATE: [
            "status hari ini", "kondisi sekarang", "posisi keuangan",
            "update state", "state hari ini",
        ],
    },

    "Environmental": {
        IntentType.CHECK_WEATHER: [
            "cuaca", "hujan", "panas", "mendung", "gerimis",
            "badai", "angin kencang", "weather", "kondisi cuaca",
            "mau hujan", "bakal hujan", "cerah nggak",
            "perlu payung", "bawa jas hujan",
        ],
    },

    "The Planner": {
        IntentType.CREATE_SCHEDULE: [
            "ingetin", "ingatin", "reminder", "pengingat",
            "jadwalin", "jadwal", "schedule", "set alarm",
            "besok jam", "hari ini jam", "nanti jam",
            "buatin pengingat", "catat jadwal", "reservasi waktu",
            "jangan lupa", "bikin penginat",
        ],
    },

    "The Archivist": {
        IntentType.SAVE_NOTE: [
            "catat ini", "simpan ini", "tulis ini",
            "catat catatan", "bikin catatan", "buat catatan",
            "save note", "note ini", "arsip",
        ],
        IntentType.SEARCH_NOTE: [
            "cari catatan", "temuin catatan", "ada catatan",
            "catatan soal", "pernah catat", "apa yang dicatat",
            "tunjukin catatan", "lihat catatan",
        ],
    },

    "Demand Analytics": {
        IntentType.ANALYZE_DEMAND: [
            "zona mana", "area mana", "hotspot", "ramai di mana",
            "di mana orderan", "titik jemput", "pickup mana",
            "ngetem di mana", "pindah ke mana",
            "permintaan tinggi", "demand mana", "peluang di mana",
            "opportunity", "analisis permintaan",
        ],
    },
}


# ============================================================
# CONTEXT EXTRACTORS — Ekstraksi konteks dari kalimat input
# ============================================================

def _extract_amount(text: str) -> Optional[float]:
    """Ekstrak nominal uang dari teks."""
    # Pola: "250 ribu", "250rb", "250.000", "Rp 250000"
    patterns = [
        r'(?:rp\.?\s*)?(\d+(?:[.,]\d+)*)\s*(?:juta|jt)',    # juta
        r'(?:rp\.?\s*)?(\d+(?:[.,]\d+)*)\s*(?:ribu|rb|k)',  # ribu
        r'(?:rp\.?\s*)?(\d{1,3}(?:\.\d{3})+)',               # format titik: 250.000
        r'(?:rp\.?\s*)?(\d+(?:,\d+)?)',                       # angka biasa
    ]
    text_lower = text.lower()
    for i, pattern in enumerate(patterns):
        match = re.search(pattern, text_lower)
        if match:
            raw = match.group(1).replace(",", "").replace(".", "")
            try:
                amount = float(raw)
                if i == 0:   # juta
                    amount *= 1_000_000
                elif i == 1: # ribu
                    amount *= 1_000
                return amount
            except ValueError:
                continue
    return None


def _extract_location(text: str) -> Optional[str]:
    """Ekstrak nama lokasi/zona dari teks."""
    # Cari kata setelah preposisi lokasi umum
    location_patterns = [
        r'(?:di|ke|dari|area|zona|daerah)\s+([A-Za-z][A-Za-z\s]{2,20})',
        r'(?:Sudirman|Kuningan|Kemayoran|Blok\s*M|Tanah\s*Abang|Gatot\s*Subroto|'
        r'Semanggi|Kebayoran|Menteng|Cikini|Senen|Mangga\s*Dua|Grogol)',
    ]
    for pattern in location_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return None


def _extract_datetime_hint(text: str) -> Optional[str]:
    """Ekstrak petunjuk waktu dari teks."""
    time_patterns = [
        r'besok\s+jam\s+\d{1,2}(?::\d{2})?(?:\s*(?:pagi|siang|sore|malam))?',
        r'hari\s+ini\s+jam\s+\d{1,2}(?::\d{2})?',
        r'jam\s+\d{1,2}(?::\d{2})?(?:\s*(?:pagi|siang|sore|malam))?',
        r'nanti\s+(?:pagi|siang|sore|malam)',
        r'(?:senin|selasa|rabu|kamis|jumat|sabtu|minggu)\s+jam\s+\d{1,2}',
    ]
    for pattern in time_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return None


def _extract_service_type(text: str) -> Optional[str]:
    """Ekstrak jenis layanan dari teks."""
    text_lower = text.lower()
    if any(w in text_lower for w in ["food", "makanan", "makan", "pesan makan"]):
        return "food"
    if any(w in text_lower for w in ["paket", "package", "pengiriman", "kirim barang"]):
        return "package"
    if any(w in text_lower for w in ["ride", "motor", "ojek", "antar jemput"]):
        return "ride"
    return None


# ============================================================
# INTENT ANALYZER
# ============================================================

class IntentAnalyzer:
    """
    Menganalisis input natural dari pengguna dan mendeteksi
    satu atau lebih intent yang harus ditangani sub-agen.

    Strategi: Keyword matching deterministik (bukan AI probabilistik).
    Setiap intent yang terdeteksi akan menghasilkan satu DetectedIntent.
    """

    def analyze(self, user_input: str) -> List[DetectedIntent]:
        """
        Analisis teks input dan kembalikan list intent yang terdeteksi.

        Args:
            user_input: Teks natural dari pengguna.

        Returns:
            List[DetectedIntent] — bisa lebih dari satu jika multi-task.
                                   Kosong jika tidak ada intent yang dikenal.
        """
        text_lower = user_input.lower()
        detected: List[DetectedIntent] = []
        seen_agents: set[str] = set()  # Hindari duplikasi agen untuk intent serupa

        for agent_name, intent_map in ROUTING_RULES.items():
            for intent_type, keywords in intent_map.items():
                matched_keywords = [kw for kw in keywords if kw in text_lower]

                if not matched_keywords:
                    continue

                # Hindari duplikasi — satu agen hanya masuk sekali per analisis
                if agent_name in seen_agents:
                    continue

                # Tentukan confidence berdasarkan jumlah keyword yang cocok
                if len(matched_keywords) >= 3:
                    confidence = "high"
                elif len(matched_keywords) == 2:
                    confidence = "medium"
                else:
                    confidence = "low"

                # Ekstrak konteks tambahan dari input
                context = self._extract_context(user_input, intent_type)

                intent = DetectedIntent(
                    intent_type=intent_type,
                    target_agent=agent_name,
                    extracted_context=context,
                    raw_excerpt=", ".join(matched_keywords[:3]),
                    confidence=confidence,
                )
                detected.append(intent)
                seen_agents.add(agent_name)

                logger.log_agent_event(
                    f"INTENT_DETECTED: {intent_type.value} → {agent_name} "
                    f"(confidence={confidence}, keywords={matched_keywords[:3]})",
                    agent_name="Bang Jek",
                )

        if not detected:
            logger.warning(
                "[ROUTER] Tidak ada intent yang terdeteksi dari input pengguna.",
                input_preview=user_input[:80],
            )

        return detected

    def _extract_context(self, text: str, intent_type: IntentType) -> dict:
        """Ekstrak konteks relevan berdasarkan tipe intent."""
        context: dict = {}

        if intent_type == IntentType.RECORD_TRANSACTION:
            amount = _extract_amount(text)
            if amount:
                context["amount"] = amount
            service = _extract_service_type(text)
            if service:
                context["service_type"] = service
            location = _extract_location(text)
            if location:
                context["zone"] = location
            context["raw_input"] = text

        elif intent_type == IntentType.CHECK_WEATHER:
            location = _extract_location(text)
            if location:
                context["location"] = location
            else:
                context["location"] = "Jakarta"  # Default fallback

        elif intent_type == IntentType.CREATE_SCHEDULE:
            datetime_hint = _extract_datetime_hint(text)
            if datetime_hint:
                context["datetime_hint"] = datetime_hint
            context["raw_input"] = text

        elif intent_type in (IntentType.SAVE_NOTE, IntentType.SEARCH_NOTE):
            context["raw_input"] = text

        elif intent_type == IntentType.ANALYZE_DEMAND:
            location = _extract_location(text)
            if location:
                context["zone_hint"] = location
            context["raw_input"] = text

        elif intent_type in (IntentType.GET_FINANCIAL_REPORT, IntentType.GET_DAILY_STATE):
            context["report_period"] = "daily"  # Default
            if any(w in text.lower() for w in ["minggu", "weekly", "7 hari"]):
                context["report_period"] = "weekly"
            elif any(w in text.lower() for w in ["bulan", "monthly", "30 hari"]):
                context["report_period"] = "monthly"

        return context


# ============================================================
# TASK PLANNER
# ============================================================

class TaskPlanner:
    """
    Menyusun TaskPlan dari list DetectedIntent yang dihasilkan IntentAnalyzer.

    Logika paralel vs sekuensial:
    - PARALLEL  : Default untuk semua tugas yang tidak saling bergantung.
    - SEQUENTIAL: Digunakan HANYA jika output satu agen diperlukan sebagai
                  input agen lain (contoh: cuaca → lalu analisis demand berbasis cuaca).

    Saat ini semua task diasumsikan paralel karena sub-agen tidak saling
    bergantung dalam skenario PRD utama.
    """

    # Pasangan (trigger_agent, dependent_agent) yang membutuhkan eksekusi sekuensial.
    # Key = agen yang harus selesai dulu, Value = agen yang menunggu outputnya.
    SEQUENTIAL_DEPENDENCIES: list[Tuple[str, str]] = [
        # Contoh: Jika analisis demand bergantung pada kondisi cuaca dulu
        # ("Environmental", "Demand Analytics"),  # Aktifkan jika diperlukan
    ]

    def build_plan(self, intents: List[DetectedIntent], user_input: str) -> TaskPlan:
        """
        Bangun TaskPlan dari list DetectedIntent.

        Args:
            intents: Hasil IntentAnalyzer.analyze()
            user_input: Input original pengguna (disertakan ke context delegasi)

        Returns:
            TaskPlan siap dieksekusi oleh BangJekOrchestrator.
        """
        if not intents:
            # Tidak ada intent terdeteksi — kembalikan task plan kosong (Bang Jek akan
            # menjawab sendiri dengan klarifikasi ke pengguna)
            return TaskPlan(
                delegations=[],
                execution_mode=ExecutionMode.PARALLEL,
                total_tasks=0,
                plan_summary="Tidak ada sub-agen yang dibutuhkan.",
            )

        delegations: List[AgentDelegation] = []
        for intent in intents:
            delegation = AgentDelegation(
                agent_name=intent.target_agent,
                task=self._build_task_description(intent, user_input),
                context={
                    **intent.extracted_context,
                    "intent_type": intent.intent_type.value,
                    "original_input": user_input,
                },
                priority=self._determine_priority(intent.intent_type),
            )
            delegations.append(delegation)

        # Tentukan execution mode
        mode = self._determine_execution_mode(intents)

        plan = TaskPlan(
            delegations=delegations,
            execution_mode=mode,
            total_tasks=len(delegations),
            plan_summary=self._summarize_plan(intents, mode),
        )

        logger.log_agent_event(
            f"TASK_PLAN_BUILT: {plan.total_tasks} task(s), mode={mode.value}",
            agent_name="Bang Jek",
            agents=[(d.agent_name) for d in delegations],
        )

        return plan

    def _build_task_description(self, intent: DetectedIntent, user_input: str) -> str:
        """Buat deskripsi tugas yang jelas untuk sub-agen."""
        templates = {
            IntentType.RECORD_TRANSACTION: (
                "Catat transaksi pendapatan berdasarkan input: '{raw}'. "
                "Amount: {amount}. Service: {service}. Zone: {zone}."
            ),
            IntentType.GET_FINANCIAL_REPORT: (
                "Buat laporan keuangan periode '{period}' dan kembalikan data terstruktur."
            ),
            IntentType.GET_DAILY_STATE: (
                "Ambil snapshot state harian pengemudi saat ini."
            ),
            IntentType.CHECK_WEATHER: (
                "Cek kondisi cuaca real-time di lokasi '{location}' dan berikan alert level."
            ),
            IntentType.CREATE_SCHEDULE: (
                "Buat entri jadwal/pengingat berdasarkan: '{raw}'. "
                "Waktu hint: '{datetime}'."
            ),
            IntentType.SAVE_NOTE: (
                "Simpan catatan berikut ke Google Keep: '{raw}'. "
                "Beri tag kategori yang sesuai."
            ),
            IntentType.SEARCH_NOTE: (
                "Cari catatan yang relevan dengan: '{raw}'."
            ),
            IntentType.ANALYZE_DEMAND: (
                "Analisis data permintaan zona saat ini dari BigQuery. "
                "Zone hint: '{zone}'. Kembalikan hotzone dan rekomendasi."
            ),
            IntentType.UNKNOWN: "Tangani permintaan: '{raw}'",
        }

        ctx = intent.extracted_context
        template = templates.get(intent.intent_type, templates[IntentType.UNKNOWN])

        try:
            return template.format(
                raw=user_input[:200],
                amount=ctx.get("amount", "tidak disebutkan"),
                service=ctx.get("service_type", "tidak disebutkan"),
                zone=ctx.get("zone") or ctx.get("zone_hint", "tidak disebutkan"),
                location=ctx.get("location", "Jakarta"),
                period=ctx.get("report_period", "daily"),
                datetime=ctx.get("datetime_hint", "tidak disebutkan"),
            )
        except KeyError:
            return f"Tangani intent '{intent.intent_type.value}' berdasarkan input: {user_input[:150]}"

    def _determine_priority(self, intent_type: IntentType) -> int:
        """Tentukan prioritas 1-10 berdasarkan tipe intent."""
        priority_map = {
            IntentType.RECORD_TRANSACTION: 8,    # Keuangan — prioritas tinggi
            IntentType.GET_FINANCIAL_REPORT: 7,
            IntentType.CHECK_WEATHER: 9,          # Real-time data — waktu kritis
            IntentType.ANALYZE_DEMAND: 8,
            IntentType.CREATE_SCHEDULE: 6,
            IntentType.SAVE_NOTE: 5,
            IntentType.SEARCH_NOTE: 5,
            IntentType.GET_DAILY_STATE: 6,
            IntentType.UNKNOWN: 3,
        }
        return priority_map.get(intent_type, 5)

    def _determine_execution_mode(self, intents: List[DetectedIntent]) -> ExecutionMode:
        """
        Tentukan apakah tasks dijalankan paralel atau sekuensial.
        Default adalah PARALLEL kecuali ada dependency eksplisit.
        """
        if len(intents) <= 1:
            return ExecutionMode.PARALLEL  # Satu task — mode tidak relevan

        detected_agents = {i.target_agent for i in intents}

        for trigger, dependent in self.SEQUENTIAL_DEPENDENCIES:
            if trigger in detected_agents and dependent in detected_agents:
                logger.log_agent_event(
                    f"SEQUENTIAL_MODE: {trigger} harus selesai sebelum {dependent}",
                    agent_name="Bang Jek",
                )
                return ExecutionMode.SEQUENTIAL

        return ExecutionMode.PARALLEL

    def _summarize_plan(self, intents: List[DetectedIntent], mode: ExecutionMode) -> str:
        """Buat ringkasan plan untuk logging."""
        agents = [f"{i.target_agent} ({i.intent_type.value})" for i in intents]
        return f"Eksekusi {mode.value.upper()}: {' + '.join(agents)}"
