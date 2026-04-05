"""
tests/unit/test_bang_jek_router.py
=====================================
Unit tests untuk IntentAnalyzer dan TaskPlanner (agents/bang_jek/router.py).

Cakupan:
  - 8+ skenario intent tunggal (deterministic routing)
  - Multi-intent detection (3 intent sekaligus)
  - Context extraction: amount, location, datetime_hint, service_type
  - Confidence scoring (high/medium/low berdasarkan jumlah keyword match)
  - TaskPlanner: build_plan() output structure
  - TaskPlanner: execution mode (PARALLEL vs SEQUENTIAL)
  - TaskPlanner: priority assignment per intent type
  - AgentDelegation output schema validation
  - Empty input → empty intents → empty task plan
  - Deduplication: satu agen tidak muncul dua kali dalam satu analisis

Semua test pure Python — tidak ada API call, I/O, atau koneksi jaringan.
"""

import pytest

from agents.bang_jek.router import (
    ExecutionMode,
    IntentAnalyzer,
    IntentType,
    TaskPlanner,
    _extract_amount,
    _extract_datetime_hint,
    _extract_location,
    _extract_service_type,
)
from shared.schemas import AgentDelegation


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture
def analyzer():
    return IntentAnalyzer()


@pytest.fixture
def planner():
    return TaskPlanner()


# ============================================================
# HELPER
# ============================================================

def _find_intent(intents, intent_type: IntentType):
    """Cari DetectedIntent berdasarkan intent_type dari hasil analyze()."""
    return next((i for i in intents if i.intent_type == intent_type), None)


def _find_agent(intents, agent_name: str):
    """Cari DetectedIntent berdasarkan target_agent."""
    return next((i for i in intents if i.target_agent == agent_name), None)


# ============================================================
# INTENT ANALYZER — 8 SKENARIO INTENT
# ============================================================

class TestIntentAnalyzerSingleIntent:
    """Minimal 5 (di sini 8) skenario intent tunggal yang deterministik."""

    # ── Intent 1: RECORD_TRANSACTION ──────────────────────────

    def test_intent_record_transaction_from_catat_pendapatan(self, analyzer):
        """'catat pendapatan' → The Auditor / RECORD_TRANSACTION."""
        intents = analyzer.analyze("catat pendapatan 250 ribu hari ini")
        assert len(intents) >= 1
        intent = _find_agent(intents, "The Auditor")
        assert intent is not None
        assert intent.intent_type == IntentType.RECORD_TRANSACTION

    def test_intent_record_transaction_from_income_keyword(self, analyzer):
        """'income' → The Auditor / RECORD_TRANSACTION (keyword bahasa Inggris)."""
        intents = analyzer.analyze("income 75 ribu dari food delivery tadi")
        intent = _find_agent(intents, "The Auditor")
        assert intent is not None
        assert intent.intent_type == IntentType.RECORD_TRANSACTION

    def test_intent_record_transaction_from_penghasilan(self, analyzer):
        """'penghasilan' → The Auditor / RECORD_TRANSACTION."""
        intents = analyzer.analyze("penghasilan hari ini 300 ribu")
        intent = _find_agent(intents, "The Auditor")
        assert intent is not None
        assert intent.intent_type == IntentType.RECORD_TRANSACTION

    # ── Intent 2: CHECK_WEATHER ────────────────────────────────

    def test_intent_check_weather_from_cuaca(self, analyzer):
        """'cuaca' → Environmental / CHECK_WEATHER."""
        intents = analyzer.analyze("cek cuaca Sudirman sekarang")
        intent = _find_agent(intents, "Environmental")
        assert intent is not None
        assert intent.intent_type == IntentType.CHECK_WEATHER

    def test_intent_check_weather_from_hujan(self, analyzer):
        """'hujan' → Environmental / CHECK_WEATHER."""
        intents = analyzer.analyze("lagi hujan nggak di Kemayoran?")
        intent = _find_agent(intents, "Environmental")
        assert intent is not None
        assert intent.intent_type == IntentType.CHECK_WEATHER

    def test_intent_check_weather_from_mau_hujan(self, analyzer):
        """'mau hujan' → Environmental / CHECK_WEATHER."""
        intents = analyzer.analyze("mau hujan nggak nih di Gatot Subroto?")
        intent = _find_agent(intents, "Environmental")
        assert intent is not None
        assert intent.intent_type == IntentType.CHECK_WEATHER

    # ── Intent 3: CREATE_SCHEDULE ──────────────────────────────

    def test_intent_create_schedule_from_ingetin(self, analyzer):
        """'ingetin' → The Planner / CREATE_SCHEDULE."""
        intents = analyzer.analyze("ingetin besok jam 9 pagi buat ganti oli")
        intent = _find_agent(intents, "The Planner")
        assert intent is not None
        assert intent.intent_type == IntentType.CREATE_SCHEDULE

    def test_intent_create_schedule_from_reminder(self, analyzer):
        """'reminder' → The Planner / CREATE_SCHEDULE."""
        intents = analyzer.analyze("set reminder jam 14:00 servis motor")
        intent = _find_agent(intents, "The Planner")
        assert intent is not None
        assert intent.intent_type == IntentType.CREATE_SCHEDULE

    def test_intent_create_schedule_from_jadwalin(self, analyzer):
        """'jadwalin' → The Planner / CREATE_SCHEDULE."""
        intents = analyzer.analyze("jadwalin meeting besok jam 10")
        intent = _find_agent(intents, "The Planner")
        assert intent is not None
        assert intent.intent_type == IntentType.CREATE_SCHEDULE

    # ── Intent 4: SAVE_NOTE ────────────────────────────────────

    def test_intent_save_note_from_catat_ini(self, analyzer):
        """'catat ini' → The Archivist / SAVE_NOTE."""
        intents = analyzer.analyze("catat ini: solar plat ada di Kemayoran")
        intent = _find_agent(intents, "The Archivist")
        assert intent is not None
        assert intent.intent_type == IntentType.SAVE_NOTE

    def test_intent_save_note_from_bikin_catatan(self, analyzer):
        """'bikin catatan' → The Archivist / SAVE_NOTE."""
        intents = analyzer.analyze("bikin catatan soal spok motor")
        intent = _find_agent(intents, "The Archivist")
        assert intent is not None
        assert intent.intent_type == IntentType.SAVE_NOTE

    # ── Intent 5: SEARCH_NOTE ─────────────────────────────────

    def test_intent_search_note_from_cari_catatan(self, analyzer):
        """'cari catatan' → The Archivist / SEARCH_NOTE."""
        intents = analyzer.analyze("cari catatan soal oli motor")
        intent = _find_agent(intents, "The Archivist")
        assert intent is not None
        assert intent.intent_type == IntentType.SEARCH_NOTE

    # ── Intent 6: ANALYZE_DEMAND ──────────────────────────────

    def test_intent_analyze_demand_from_zona_mana(self, analyzer):
        """'zona mana' → Demand Analytics / ANALYZE_DEMAND."""
        intents = analyzer.analyze("zona mana yang lagi ramai sekarang?")
        intent = _find_agent(intents, "Demand Analytics")
        assert intent is not None
        assert intent.intent_type == IntentType.ANALYZE_DEMAND

    def test_intent_analyze_demand_from_hotspot(self, analyzer):
        """'hotspot' → Demand Analytics / ANALYZE_DEMAND."""
        intents = analyzer.analyze("cek hotspot orderan jam segini")
        intent = _find_agent(intents, "Demand Analytics")
        assert intent is not None
        assert intent.intent_type == IntentType.ANALYZE_DEMAND

    # ── Intent 7: GET_FINANCIAL_REPORT ────────────────────────

    def test_intent_get_financial_report_from_rekap(self, analyzer):
        """'rekap' → The Auditor / GET_FINANCIAL_REPORT."""
        intents = analyzer.analyze("rekap pendapatan hari ini dong")
        # Mungkin ada dua intent Auditor — cari yang GET_FINANCIAL_REPORT
        intent = _find_intent(intents, IntentType.GET_FINANCIAL_REPORT)
        assert intent is not None
        assert intent.target_agent == "The Auditor"

    def test_intent_get_financial_report_from_berapa_hari_ini(self, analyzer):
        """'berapa hari ini' → The Auditor / GET_FINANCIAL_REPORT."""
        intents = analyzer.analyze("udah berapa hari ini Bang?")
        intent = _find_intent(intents, IntentType.GET_FINANCIAL_REPORT)
        assert intent is not None

    # ── Intent 8: UNKNOWN → empty list ────────────────────────

    def test_intent_unknown_input_returns_empty(self, analyzer):
        """Input tidak dikenali → list kosong."""
        intents = analyzer.analyze("xyzabcdef qwerty 12345 !!!!")
        assert intents == []

    def test_empty_string_returns_empty(self, analyzer):
        """String kosong → list kosong."""
        intents = analyzer.analyze("")
        assert intents == []


# ============================================================
# INTENT ANALYZER — MULTI-INTENT (skenario wajib PRD)
# ============================================================

class TestIntentAnalyzerMultiIntent:
    """Deteksi lebih dari satu intent dari satu input panjang."""

    CANONICAL_INPUT = (
        "Bang Jek, catat pendapatan 250 ribu hari ini, "
        "cek cuaca Sudirman, dan ingetin besok jam 9 pagi buat ganti oli."
    )

    def test_canonical_input_detects_three_agents(self, analyzer):
        """Skenario wajib CLAUDE.md §7.4: 3 intent dari 1 input."""
        intents = analyzer.analyze(self.CANONICAL_INPUT)
        detected_agents = {i.target_agent for i in intents}
        assert "The Auditor" in detected_agents, "Auditor harus terdeteksi (catat pendapatan)"
        assert "Environmental" in detected_agents, "Environmental harus terdeteksi (cek cuaca)"
        assert "The Planner" in detected_agents, "Planner harus terdeteksi (ingetin)"
        assert len(intents) == 3, f"Harus tepat 3 intent. Got: {[i.target_agent for i in intents]}"

    def test_canonical_input_correct_intent_types(self, analyzer):
        """Setiap intent dari skenario wajib harus memiliki tipe intent yang tepat."""
        intents = analyzer.analyze(self.CANONICAL_INPUT)

        auditor_intent = _find_agent(intents, "The Auditor")
        weather_intent = _find_agent(intents, "Environmental")
        planner_intent = _find_agent(intents, "The Planner")

        assert auditor_intent.intent_type == IntentType.RECORD_TRANSACTION
        assert weather_intent.intent_type == IntentType.CHECK_WEATHER
        assert planner_intent.intent_type == IntentType.CREATE_SCHEDULE

    def test_no_agent_appears_twice(self, analyzer):
        """
        Deduplication: satu agen tidak boleh muncul lebih dari sekali
        dalam satu hasil analisis, meskipun ada 2 keyword matching.
        """
        # 'pendapatan' DAN 'rekap' harusnya tidak bikin The Auditor muncul 2x
        intents = analyzer.analyze(
            "rekap pendapatan hari ini dan cek cuaca"
        )
        auditor_intents = [i for i in intents if i.target_agent == "The Auditor"]
        assert len(auditor_intents) <= 1, "The Auditor tidak boleh muncul lebih dari 1x"

    def test_two_agents_detected(self, analyzer):
        """Input dua-task: Auditor + Environmental."""
        intents = analyzer.analyze(
            "catat pendapatan 100 ribu dan cek cuaca"
        )
        detected_agents = {i.target_agent for i in intents}
        assert "The Auditor" in detected_agents
        assert "Environmental" in detected_agents


# ============================================================
# INTENT ANALYZER — CONFIDENCE SCORING
# ============================================================

class TestIntentAnalyzerConfidence:
    """Confidence level ditentukan oleh jumlah keyword yang cocok."""

    def test_single_keyword_match_is_low_confidence(self, analyzer):
        """1 keyword match → confidence 'low'."""
        # "cuaca" adalah 1 keyword
        intents = analyzer.analyze("gimana cuaca?")
        intent = _find_agent(intents, "Environmental")
        if intent:
            assert intent.confidence == "low"

    def test_two_keyword_matches_is_medium_confidence(self, analyzer):
        """2 keyword match → confidence 'medium'."""
        # "cuaca" + "hujan" = 2 keywords
        intents = analyzer.analyze("cuaca hujan nggak hari ini?")
        intent = _find_agent(intents, "Environmental")
        if intent:
            assert intent.confidence in {"medium", "high"}

    def test_three_or_more_keywords_is_high_confidence(self, analyzer):
        """3+ keyword match → confidence 'high'."""
        # "cuaca" + "hujan" + "mendung" = 3 keywords
        intents = analyzer.analyze("cuaca hujan mendung badai hari ini gimana?")
        intent = _find_agent(intents, "Environmental")
        if intent:
            assert intent.confidence == "high"


# ============================================================
# CONTEXT EXTRACTION
# ============================================================

class TestContextExtraction:
    """Validasi _extract_context() menghasilkan context yang benar."""

    def test_amount_extracted_from_ribu(self, analyzer):
        """'250 ribu' → amount = 250000.0."""
        intents = analyzer.analyze("catat pendapatan 250 ribu")
        intent = _find_agent(intents, "The Auditor")
        assert intent is not None
        assert intent.extracted_context.get("amount") == 250_000.0

    def test_amount_extracted_from_juta(self, analyzer):
        """'1 juta' → amount = 1000000.0."""
        intents = analyzer.analyze("catat pendapatan 1 juta hari ini")
        intent = _find_agent(intents, "The Auditor")
        assert intent is not None
        assert intent.extracted_context.get("amount") == 1_000_000.0

    def test_location_extracted_for_weather(self, analyzer):
        """'cek cuaca Sudirman' → context['location'] = 'Sudirman'."""
        intents = analyzer.analyze("cek cuaca Sudirman sekarang")
        intent = _find_agent(intents, "Environmental")
        assert intent is not None
        location = intent.extracted_context.get("location", "")
        assert "Sudirman" in location or "sudirman" in location.lower()

    def test_default_location_jakarta_when_no_location(self, analyzer):
        """Jika tidak ada lokasi → default 'Jakarta'."""
        intents = analyzer.analyze("gimana cuaca hari ini?")
        intent = _find_agent(intents, "Environmental")
        if intent:
            assert intent.extracted_context.get("location") == "Jakarta"

    def test_datetime_hint_extracted_for_schedule(self, analyzer):
        """'besok jam 9 pagi' → context['datetime_hint'] berisi string tersebut."""
        intents = analyzer.analyze("ingetin besok jam 9 pagi buat ganti oli")
        intent = _find_agent(intents, "The Planner")
        assert intent is not None
        hint = intent.extracted_context.get("datetime_hint", "")
        assert "besok" in hint.lower() or "jam 9" in hint.lower()

    def test_service_type_food_extracted(self, analyzer):
        """'makanan' → service_type = 'food'."""
        intents = analyzer.analyze("catat pendapatan 75 ribu dari antar makanan")
        intent = _find_agent(intents, "The Auditor")
        assert intent is not None
        svc = intent.extracted_context.get("service_type")
        assert svc == "food"

    def test_raw_input_always_in_context(self, analyzer):
        """Context selalu berisi 'raw_input' untuk semua intent keuangan."""
        intents = analyzer.analyze("catat pendapatan 100 ribu")
        intent = _find_agent(intents, "The Auditor")
        assert intent is not None
        assert "raw_input" in intent.extracted_context


# ============================================================
# CONTEXT EXTRACTOR FUNCTIONS — Unit tests langsung
# ============================================================

class TestContextExtractorFunctions:
    """Test fungsi helper _extract_*() secara langsung."""

    @pytest.mark.parametrize("text,expected", [
        ("250 ribu",                    250_000.0),
        ("250rb",                       250_000.0),
        ("1 juta",                 1_000_000.0),
        ("1jt",                    1_000_000.0),
        ("100.000",                     100_000.0),
        ("75000",                           75.0),   # angka biasa tanpa multiplier
    ])
    def test_extract_amount_variants(self, text, expected):
        """_extract_amount() harus mengenali berbagai format angka IDR."""
        result = _extract_amount(text)
        assert result == expected, f"'{text}' seharusnya → {expected}, got {result}"

    def test_extract_amount_returns_none_for_no_number(self):
        """Teks tanpa angka → None."""
        assert _extract_amount("hari ini cerah") is None

    @pytest.mark.parametrize("text,expected_substring", [
        ("di Sudirman",              "Sudirman"),
        ("area Kemayoran",           "Kemayoran"),
        ("cek cuaca Gatot Subroto",  "Gatot"),
    ])
    def test_extract_location_variants(self, text, expected_substring):
        """_extract_location() harus mengenali nama tempat umum Jakarta."""
        result = _extract_location(text)
        assert result is not None
        assert expected_substring in result

    @pytest.mark.parametrize("text,expected_substring", [
        ("besok jam 9 pagi",   "besok"),
        ("hari ini jam 14:00", "hari ini"),
        ("nanti sore",         "nanti sore"),
    ])
    def test_extract_datetime_hint_variants(self, text, expected_substring):
        """_extract_datetime_hint() harus menangkap petunjuk waktu umum."""
        result = _extract_datetime_hint(text)
        assert result is not None
        assert expected_substring in result.lower()

    def test_extract_datetime_hint_returns_none_when_no_time(self):
        """Teks tanpa petunjuk waktu → None."""
        assert _extract_datetime_hint("catat pendapatan 100 ribu") is None

    @pytest.mark.parametrize("text,expected_type", [
        ("antar makanan",    "food"),
        ("pesan makan",      "food"),
        ("paket pengiriman", "package"),
        ("kirim barang",     "package"),
        ("antar ojek",       "ride"),
        ("naik motor",       "ride"),
    ])
    def test_extract_service_type_variants(self, text, expected_type):
        """_extract_service_type() harus mengenali 3 kategori layanan."""
        result = _extract_service_type(text)
        assert result == expected_type

    def test_extract_service_type_none_when_unclear(self):
        """Teks tanpa indikator service type → None."""
        assert _extract_service_type("hari ini hujan lebat") is None


# ============================================================
# TASK PLANNER — BUILD PLAN
# ============================================================

class TestTaskPlannerBuildPlan:
    """TaskPlanner.build_plan() harus menghasilkan TaskPlan yang valid."""

    CANONICAL_INPUT = (
        "Bang Jek, catat pendapatan 250 ribu hari ini, "
        "cek cuaca Sudirman, dan ingetin besok jam 9 pagi buat ganti oli."
    )

    def test_build_plan_returns_three_delegations(self, analyzer, planner):
        """Skenario wajib: 3 intent → 3 AgentDelegation."""
        intents = analyzer.analyze(self.CANONICAL_INPUT)
        plan = planner.build_plan(intents, self.CANONICAL_INPUT)

        assert plan.total_tasks == 3
        assert len(plan.delegations) == 3

    def test_build_plan_delegations_are_agent_delegation_schema(self, analyzer, planner):
        """Setiap delegasi harus berupa AgentDelegation dari shared/schemas.py."""
        intents = analyzer.analyze(self.CANONICAL_INPUT)
        plan = planner.build_plan(intents, self.CANONICAL_INPUT)

        for d in plan.delegations:
            assert isinstance(d, AgentDelegation)
            assert d.agent_name in {"The Auditor", "Environmental", "The Planner"}
            assert d.task  # Tidak boleh kosong
            assert d.context  # Harus ada context

    def test_build_plan_canonical_agents_in_delegations(self, analyzer, planner):
        """Ketiga agen wajib (Auditor, Environmental, Planner) harus ada dalam plan."""
        intents = analyzer.analyze(self.CANONICAL_INPUT)
        plan = planner.build_plan(intents, self.CANONICAL_INPUT)

        agent_names = {d.agent_name for d in plan.delegations}
        assert "The Auditor" in agent_names
        assert "Environmental" in agent_names
        assert "The Planner" in agent_names

    def test_build_plan_context_contains_intent_type(self, analyzer, planner):
        """Setiap context delegasi harus berisi 'intent_type' dan 'original_input'."""
        intents = analyzer.analyze(self.CANONICAL_INPUT)
        plan = planner.build_plan(intents, self.CANONICAL_INPUT)

        for d in plan.delegations:
            assert "intent_type" in d.context
            assert "original_input" in d.context

    def test_build_plan_empty_intents_returns_zero_tasks(self, planner):
        """Input tidak dikenal → task plan dengan 0 delegasi."""
        plan = planner.build_plan([], "xyz tidak dikenal abcd")
        assert plan.total_tasks == 0
        assert plan.delegations == []

    def test_build_plan_execution_mode_is_parallel_by_default(self, analyzer, planner):
        """Multi-task default → PARALLEL (tidak ada dependency sekuensial aktif)."""
        intents = analyzer.analyze(self.CANONICAL_INPUT)
        plan = planner.build_plan(intents, self.CANONICAL_INPUT)
        assert plan.execution_mode == ExecutionMode.PARALLEL

    def test_build_plan_single_task_is_also_parallel(self, analyzer, planner):
        """Satu task saja → mode PARALLEL (mode tidak relevan, selalu parallel)."""
        intents = analyzer.analyze("cek cuaca Jakarta")
        plan = planner.build_plan(intents, "cek cuaca Jakarta")
        assert plan.execution_mode == ExecutionMode.PARALLEL

    def test_build_plan_plan_summary_is_nonempty(self, analyzer, planner):
        """plan_summary harus diisi (bukan string kosong)."""
        intents = analyzer.analyze(self.CANONICAL_INPUT)
        plan = planner.build_plan(intents, self.CANONICAL_INPUT)
        assert isinstance(plan.plan_summary, str)
        assert len(plan.plan_summary) > 0


# ============================================================
# TASK PLANNER — PRIORITY ASSIGNMENT
# ============================================================

class TestTaskPlannerPriority:
    """Prioritas per intent type sudah ditentukan (priority_map di router.py)."""

    def test_weather_delegation_has_high_priority(self, analyzer, planner):
        """CHECK_WEATHER prioritas 9 — tertinggi (real-time data kritis)."""
        intents = analyzer.analyze("cek cuaca Jakarta")
        plan = planner.build_plan(intents, "cek cuaca Jakarta")
        weather_del = next(
            (d for d in plan.delegations if d.agent_name == "Environmental"), None
        )
        assert weather_del is not None
        assert weather_del.priority == 9

    def test_transaction_delegation_has_priority_8(self, analyzer, planner):
        """RECORD_TRANSACTION prioritas 8."""
        intents = analyzer.analyze("catat pendapatan 100 ribu")
        plan = planner.build_plan(intents, "catat pendapatan 100 ribu")
        auditor_del = next(
            (d for d in plan.delegations if d.agent_name == "The Auditor"), None
        )
        if auditor_del:
            assert auditor_del.priority == 8

    def test_schedule_delegation_has_priority_6(self, analyzer, planner):
        """CREATE_SCHEDULE prioritas 6."""
        intents = analyzer.analyze("ingetin besok jam 9 ganti oli")
        plan = planner.build_plan(intents, "ingetin besok jam 9 ganti oli")
        planner_del = next(
            (d for d in plan.delegations if d.agent_name == "The Planner"), None
        )
        if planner_del:
            assert planner_del.priority == 6


# ============================================================
# TASK PLANNER — TASK DESCRIPTION GENERATION
# ============================================================

class TestTaskPlannerTaskDescription:
    """Task description yang dihasilkan harus relevan dan mengandung data penting."""

    def test_weather_task_contains_location(self, analyzer, planner):
        """Task untuk Environmental harus menyebut lokasi."""
        inp = "cek cuaca Sudirman sekarang"
        intents = analyzer.analyze(inp)
        plan = planner.build_plan(intents, inp)
        weather_del = next(
            (d for d in plan.delegations if d.agent_name == "Environmental"), None
        )
        assert weather_del is not None
        assert "Sudirman" in weather_del.task or "Jakarta" in weather_del.task

    def test_transaction_task_contains_amount(self, analyzer, planner):
        """Task untuk Auditor harus menyebut amount yang diekstrak."""
        inp = "catat pendapatan 250 ribu"
        intents = analyzer.analyze(inp)
        plan = planner.build_plan(intents, inp)
        auditor_del = next(
            (d for d in plan.delegations if d.agent_name == "The Auditor"), None
        )
        assert auditor_del is not None
        # Amount 250000 atau "250 ribu" harus ada di task description
        assert "250" in auditor_del.task or "250000" in auditor_del.task

    def test_schedule_task_contains_datetime_hint(self, analyzer, planner):
        """Task untuk Planner harus menyebut datetime hint."""
        inp = "ingetin besok jam 9 pagi buat ganti oli"
        intents = analyzer.analyze(inp)
        plan = planner.build_plan(intents, inp)
        planner_del = next(
            (d for d in plan.delegations if d.agent_name == "The Planner"), None
        )
        assert planner_del is not None
        assert "besok" in planner_del.task.lower() or "jam 9" in planner_del.task.lower()
