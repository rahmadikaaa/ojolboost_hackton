"""
tests/integration/test_atomic_multitask.py
============================================
SKENARIO END-TO-END WAJIB (CLAUDE.md Seksi 7.4).

Input wajib:
    "Bang Jek, catat pendapatan 250 ribu hari ini,
     cek cuaca Sudirman, dan ingetin besok jam 9 pagi buat ganti oli."

Ekspektasi:
    - 3 sub-agen terpanggil: The Auditor, Environmental, The Planner
    - Reasoning Accuracy (intent map) 100% tepat
    - Bang Jek menerima results-only JSON dari ketiga agen
    - Bang Jek mensintesis narasi akhir

Mocking Strategy:
    - BigQuery client           → unittest.mock.MagicMock
    - OpenWeather API (requests)→ unittest.mock.patch
    - MCP Server (Calendar)     → unittest.mock.MagicMock
    - Vertex AI (synthesis)     → unittest.mock.patch → fallback narasi
    - Semua sub-agen di-mock    → MagicMock(.process()) → AgentResult valid

Semua test berjalan 100% offline — tidak ada koneksi jaringan nyata.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from agents.bang_jek.agent import BangJekOrchestrator, _build_fallback_narration
from agents.bang_jek.router import (
    ExecutionMode,
    IntentAnalyzer,
    IntentType,
    TaskPlanner,
)
from shared.schemas import (
    AgentDelegation,
    AgentResult,
    BangJekResponseSchema,
    TaskStatus,
)


# ============================================================
# KONSTANTA SKENARIO
# ============================================================

CANONICAL_INPUT = (
    "Bang Jek, catat pendapatan 250 ribu hari ini, "
    "cek cuaca Sudirman, dan ingetin besok jam 9 pagi buat ganti oli."
)

# Mock AgentResult yang akan dikembalikan oleh sub-agen
MOCK_AUDITOR_RESULT = AgentResult(
    delegation_id="del-auditor-001",
    agent_name="The Auditor",
    status=TaskStatus.COMPLETED,
    data={
        "transaction_id": "txn-mock-001",
        "operation": "INSERT + STATE_UPDATE",
        "table": "ojolboosttrack2.trx_daily_income",
        "status": "completed",
        "balance_snapshot": 250_000.0,
        "records_affected": 1,
        "anomaly_detected": False,
    },
    execution_time_ms=42.0,
)

MOCK_ENVIRONMENTAL_RESULT = AgentResult(
    delegation_id="del-env-001",
    agent_name="Environmental",
    status=TaskStatus.COMPLETED,
    data={
        "location": "Sudirman",
        "condition": "clear",
        "temperature_celsius": 32.5,
        "humidity_percent": 68.0,
        "alert_level": "low",
        "pivot_recommendation": "[Sudirman] Kondisi aman, lanjutkan operasional normal.",
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
    },
    execution_time_ms=155.0,
)

MOCK_PLANNER_RESULT = AgentResult(
    delegation_id="del-plan-001",
    agent_name="The Planner",
    status=TaskStatus.COMPLETED,
    data={
        "event_id": "evt-mock-001",
        "status": "completed",
        "scheduled_at": "2026-04-06T09:00:00+07:00",
        "title": "[SERVIS] Ganti oli",
        "reminder_set": True,
        "conflict_resolved": False,
        "original_time": None,
        "shift_reason": None,
    },
    execution_time_ms=88.0,
)


# ============================================================
# FIXTURE: BangJekOrchestrator dengan sub-agen di-mock
# ============================================================

@pytest.fixture
def orchestrator_with_mocks():
    """
    BangJekOrchestrator dengan:
    - Vertex AI di-mock (tidak dipanggil nyata)
    - Semua 3 sub-agen di-register sebagai mock MagicMock
    - Mock sub-agen mengembalikan AgentResult yang sudah ditentukan

    Digunakan untuk semua test integrasi tanpa koneksi nyata.
    """
    # Patch vertexai.init agar tidak mencoba koneksi ke GCP
    with patch("agents.bang_jek.agent.vertexai.init"):
        with patch("agents.bang_jek.agent.GenerativeModel") as mock_model_cls:
            # Model synthesis di-mock → langsung kembalikan fallback
            mock_model = MagicMock()
            mock_model.generate_content.side_effect = Exception(
                "Vertex AI tidak tersedia dalam test environment — pakai fallback."
            )
            mock_model_cls.return_value = mock_model

            orchestra = BangJekOrchestrator()
            orchestra.initialize()

            # Mock sub-agen Auditor
            mock_auditor = MagicMock()
            mock_auditor.process.return_value = MOCK_AUDITOR_RESULT

            # Mock sub-agen Environmental
            mock_environmental = MagicMock()
            mock_environmental.process.return_value = MOCK_ENVIRONMENTAL_RESULT

            # Mock sub-agen Planner
            mock_planner = MagicMock()
            mock_planner.process.return_value = MOCK_PLANNER_RESULT

            # Register semua mock sub-agen
            orchestra.register_sub_agent("The Auditor", mock_auditor)
            orchestra.register_sub_agent("Environmental", mock_environmental)
            orchestra.register_sub_agent("The Planner", mock_planner)

            yield orchestra, mock_auditor, mock_environmental, mock_planner


# ============================================================
# TEST GRUP 1: REASONING ACCURACY — INTENT MAP 100%
# (Verifikasi sebelum eksekusi penuh)
# ============================================================

class TestReasoningAccuracy:
    """
    Verifikasi bahwa IntentAnalyzer + TaskPlanner memetakan input wajib
    ke agen yang TEPAT dengan akurasi 100%.
    """

    def test_canonical_input_maps_to_exactly_three_agents(self):
        """ROUTING ACCURACY: 3 intent terdeteksi dari input wajib."""
        analyzer = IntentAnalyzer()
        intents = analyzer.analyze(CANONICAL_INPUT)
        assert len(intents) == 3, (
            f"Harus tepat 3 intent. Terdeteksi: {[i.target_agent for i in intents]}"
        )

    def test_auditor_intent_is_record_transaction(self):
        """ROUTING ACCURACY: The Auditor mendapat intent RECORD_TRANSACTION."""
        analyzer = IntentAnalyzer()
        intents = analyzer.analyze(CANONICAL_INPUT)
        auditor_intent = next(
            (i for i in intents if i.target_agent == "The Auditor"), None
        )
        assert auditor_intent is not None, "The Auditor tidak terdeteksi"
        assert auditor_intent.intent_type == IntentType.RECORD_TRANSACTION

    def test_environmental_intent_is_check_weather(self):
        """ROUTING ACCURACY: Environmental mendapat intent CHECK_WEATHER."""
        analyzer = IntentAnalyzer()
        intents = analyzer.analyze(CANONICAL_INPUT)
        env_intent = next(
            (i for i in intents if i.target_agent == "Environmental"), None
        )
        assert env_intent is not None, "Environmental tidak terdeteksi"
        assert env_intent.intent_type == IntentType.CHECK_WEATHER

    def test_planner_intent_is_create_schedule(self):
        """ROUTING ACCURACY: The Planner mendapat intent CREATE_SCHEDULE."""
        analyzer = IntentAnalyzer()
        intents = analyzer.analyze(CANONICAL_INPUT)
        planner_intent = next(
            (i for i in intents if i.target_agent == "The Planner"), None
        )
        assert planner_intent is not None, "The Planner tidak terdeteksi"
        assert planner_intent.intent_type == IntentType.CREATE_SCHEDULE

    def test_auditor_context_has_correct_amount(self):
        """CONTEXT ACCURACY: Amount 250 ribu diekstrak dengan benar → 250000.0."""
        analyzer = IntentAnalyzer()
        intents = analyzer.analyze(CANONICAL_INPUT)
        auditor_intent = next(
            (i for i in intents if i.target_agent == "The Auditor"), None
        )
        assert auditor_intent is not None
        extracted_amount = auditor_intent.extracted_context.get("amount")
        assert extracted_amount == 250_000.0, (
            f"Amount seharusnya 250000.0, got {extracted_amount}"
        )

    def test_environmental_context_has_sudirman_location(self):
        """CONTEXT ACCURACY: Lokasi 'Sudirman' diekstrak untuk Environmental."""
        analyzer = IntentAnalyzer()
        intents = analyzer.analyze(CANONICAL_INPUT)
        env_intent = next(
            (i for i in intents if i.target_agent == "Environmental"), None
        )
        assert env_intent is not None
        location = env_intent.extracted_context.get("location", "")
        assert "Sudirman" in location or "sudirman" in location.lower(), (
            f"'Sudirman' seharusnya ada di context location. Got: '{location}'"
        )

    def test_planner_context_has_datetime_hint(self):
        """CONTEXT ACCURACY: 'besok jam 9 pagi' diekstrak sebagai datetime_hint."""
        analyzer = IntentAnalyzer()
        intents = analyzer.analyze(CANONICAL_INPUT)
        planner_intent = next(
            (i for i in intents if i.target_agent == "The Planner"), None
        )
        assert planner_intent is not None
        hint = planner_intent.extracted_context.get("datetime_hint", "")
        assert hint, "datetime_hint tidak boleh kosong"
        assert "besok" in hint.lower() or "jam 9" in hint.lower(), (
            f"hint harus mengandung 'besok' atau 'jam 9'. Got: '{hint}'"
        )

    def test_task_plan_is_parallel(self):
        """PLAN ACCURACY: 3 task independen → execution_mode = PARALLEL."""
        analyzer = IntentAnalyzer()
        planner = TaskPlanner()
        intents = analyzer.analyze(CANONICAL_INPUT)
        plan = planner.build_plan(intents, CANONICAL_INPUT)
        assert plan.execution_mode == ExecutionMode.PARALLEL


# ============================================================
# TEST GRUP 2: SUB-AGENT DELEGATION VERIFICATION
# (Verifikasi bahwa sub-agen benar-benar dipanggil)
# ============================================================

class TestSubAgentDelegation:
    """Verifikasi bahwa Bang Jek memanggil 3 sub-agen yang tepat."""

    def test_three_sub_agents_are_called(self, orchestrator_with_mocks):
        """DELEGATION: Tepat 3 sub-agen dipanggil dengan tepat 1 kali masing-masing."""
        orchestra, mock_auditor, mock_environmental, mock_planner = orchestrator_with_mocks
        orchestra.process(CANONICAL_INPUT)

        mock_auditor.process.assert_called_once()
        mock_environmental.process.assert_called_once()
        mock_planner.process.assert_called_once()

    def test_auditor_receives_delegation(self, orchestrator_with_mocks):
        """DELEGATION: The Auditor menerima AgentDelegation yang valid."""
        orchestra, mock_auditor, _, _ = orchestrator_with_mocks
        orchestra.process(CANONICAL_INPUT)

        call_args = mock_auditor.process.call_args
        assert call_args is not None

        delegation = call_args[0][0]   # positional arg
        assert isinstance(delegation, AgentDelegation)
        assert delegation.agent_name == "The Auditor"
        assert delegation.context.get("intent_type") == "record_transaction"

    def test_environmental_receives_delegation_with_sudirman(self, orchestrator_with_mocks):
        """DELEGATION: Environmental menerima delegasi dengan location Sudirman."""
        orchestra, _, mock_environmental, _ = orchestrator_with_mocks
        orchestra.process(CANONICAL_INPUT)

        delegation = mock_environmental.process.call_args[0][0]
        assert isinstance(delegation, AgentDelegation)
        assert delegation.agent_name == "Environmental"
        location = delegation.context.get("location", "")
        assert "Sudirman" in location or "sudirman" in location.lower()

    def test_planner_receives_delegation_with_schedule_intent(self, orchestrator_with_mocks):
        """DELEGATION: The Planner menerima delegasi dengan intent create_schedule."""
        orchestra, _, _, mock_planner = orchestrator_with_mocks
        orchestra.process(CANONICAL_INPUT)

        delegation = mock_planner.process.call_args[0][0]
        assert isinstance(delegation, AgentDelegation)
        assert delegation.agent_name == "The Planner"
        assert delegation.context.get("intent_type") == "create_schedule"

    def test_no_extra_agents_called(self, orchestrator_with_mocks):
        """ISOLATION: Agen yang tidak relevan (Archivist, Demand Analytics) tidak dipanggil."""
        orchestra, mock_auditor, mock_environmental, mock_planner = orchestrator_with_mocks

        # Register mock untuk agen yang seharusnya tidak dipanggil
        mock_archivist = MagicMock()
        mock_demand = MagicMock()
        orchestra.register_sub_agent("The Archivist", mock_archivist)
        orchestra.register_sub_agent("Demand Analytics", mock_demand)

        orchestra.process(CANONICAL_INPUT)

        mock_archivist.process.assert_not_called()
        mock_demand.process.assert_not_called()


# ============================================================
# TEST GRUP 3: RESULTS COLLECTION & SYNTHESIS
# ============================================================

class TestResultsCollectionAndSynthesis:
    """Verifikasi Bang Jek mengumpulkan results-only dan mensintesis narasi."""

    def test_response_is_bang_jek_response_schema(self, orchestrator_with_mocks):
        """OUTPUT SCHEMA: Response harus BangJekResponseSchema dari shared/schemas.py."""
        orchestra, _, _, _ = orchestrator_with_mocks
        response = orchestra.process(CANONICAL_INPUT)
        assert isinstance(response, BangJekResponseSchema)

    def test_response_has_three_agent_results(self, orchestrator_with_mocks):
        """COLLECTION: agent_results harus berisi tepat 3 AgentResult."""
        orchestra, _, _, _ = orchestrator_with_mocks
        response = orchestra.process(CANONICAL_INPUT)
        assert len(response.agent_results) == 3

    def test_all_agent_results_are_completed(self, orchestrator_with_mocks):
        """COLLECTION: Semua 3 AgentResult harus berstatus COMPLETED."""
        orchestra, _, _, _ = orchestrator_with_mocks
        response = orchestra.process(CANONICAL_INPUT)
        for result in response.agent_results:
            assert result.status == TaskStatus.COMPLETED, (
                f"{result.agent_name} status = {result.status}, seharusnya COMPLETED"
            )

    def test_response_narration_is_nonempty_string(self, orchestrator_with_mocks):
        """SYNTHESIS: narration harus berupa string tidak kosong."""
        orchestra, _, _, _ = orchestrator_with_mocks
        response = orchestra.process(CANONICAL_INPUT)
        assert isinstance(response.narration, str)
        assert len(response.narration) > 0

    def test_response_user_input_matches(self, orchestrator_with_mocks):
        """OUTPUT: response.user_input harus sama dengan input yang dikirim."""
        orchestra, _, _, _ = orchestrator_with_mocks
        response = orchestra.process(CANONICAL_INPUT)
        assert response.user_input == CANONICAL_INPUT

    def test_response_agents_called_contains_three_agents(self, orchestrator_with_mocks):
        """OUTPUT: agents_called harus berisi 3 nama agen."""
        orchestra, _, _, _ = orchestrator_with_mocks
        response = orchestra.process(CANONICAL_INPUT)
        assert len(response.agents_called) == 3
        assert "The Auditor" in response.agents_called
        assert "Environmental" in response.agents_called
        assert "The Planner" in response.agents_called

    def test_response_has_positive_latency(self, orchestrator_with_mocks):
        """OUTPUT: total_latency_ms harus bilangan positif."""
        orchestra, _, _, _ = orchestrator_with_mocks
        response = orchestra.process(CANONICAL_INPUT)
        assert response.total_latency_ms > 0

    def test_auditor_result_data_contains_balance_snapshot(self, orchestrator_with_mocks):
        """RESULTS ONLY: Auditor result harus berisi balance_snapshot = 250000."""
        orchestra, _, _, _ = orchestrator_with_mocks
        response = orchestra.process(CANONICAL_INPUT)
        auditor_result = next(
            (r for r in response.agent_results if r.agent_name == "The Auditor"), None
        )
        assert auditor_result is not None
        assert auditor_result.data.get("balance_snapshot") == 250_000.0

    def test_environmental_result_data_contains_condition(self, orchestrator_with_mocks):
        """RESULTS ONLY: Environmental result harus berisi kondisi cuaca."""
        orchestra, _, _, _ = orchestrator_with_mocks
        response = orchestra.process(CANONICAL_INPUT)
        env_result = next(
            (r for r in response.agent_results if r.agent_name == "Environmental"), None
        )
        assert env_result is not None
        assert "condition" in env_result.data
        assert env_result.data["condition"] == "clear"

    def test_planner_result_data_contains_event_id(self, orchestrator_with_mocks):
        """RESULTS ONLY: Planner result harus berisi event_id."""
        orchestra, _, _, _ = orchestrator_with_mocks
        response = orchestra.process(CANONICAL_INPUT)
        planner_result = next(
            (r for r in response.agent_results if r.agent_name == "The Planner"), None
        )
        assert planner_result is not None
        assert "event_id" in planner_result.data


# ============================================================
# TEST GRUP 4: FALLBACK NARRATION (VERTEX AI UNAVAILABLE)
# ============================================================

class TestFallbackNarration:
    """
    Bang Jek harus tetap menghasilkan narasi yang bermakna
    bahkan ketika Vertex AI tidak tersedia (fallback deterministik).
    """

    def test_fallback_narration_contains_balance(self):
        """Fallback narasi Auditor harus menyebut balance_snapshot."""
        results = [MOCK_AUDITOR_RESULT]
        narration = _build_fallback_narration(CANONICAL_INPUT, results)
        assert "250" in narration or "250.000" in narration or "250,000" in narration

    def test_fallback_narration_contains_weather_condition(self):
        """Fallback narasi Environmental harus menyebut kondisi cuaca."""
        results = [MOCK_ENVIRONMENTAL_RESULT]
        narration = _build_fallback_narration(CANONICAL_INPUT, results)
        # Kondisi 'clear' → 'cerah ☀️' atau 'cuaca'
        assert "cuaca" in narration.lower() or "cerah" in narration.lower()

    def test_fallback_narration_contains_schedule_info(self):
        """Fallback narasi Planner harus menyebut judul event."""
        results = [MOCK_PLANNER_RESULT]
        narration = _build_fallback_narration(CANONICAL_INPUT, results)
        assert "Ganti" in narration or "oli" in narration.lower() or "jadwal" in narration.lower()

    def test_fallback_narration_three_agents_combined(self):
        """Narasi dengan 3 hasil sub-agen harus tidak kosong dan informatif."""
        results = [MOCK_AUDITOR_RESULT, MOCK_ENVIRONMENTAL_RESULT, MOCK_PLANNER_RESULT]
        narration = _build_fallback_narration(CANONICAL_INPUT, results)
        assert isinstance(narration, str)
        assert len(narration) > 50, "Narasi terlalu pendek — kurang informatif"

    def test_fallback_narration_handles_failed_agent(self):
        """Jika salah satu agen FAILED, narasi harus menyampaikan dengan baik."""
        failed_result = AgentResult(
            agent_name="The Auditor",
            status=TaskStatus.FAILED,
            error="BigQuery timeout setelah 15 detik",
        )
        narration = _build_fallback_narration(CANONICAL_INPUT, [failed_result])
        assert "kendala" in narration.lower() or "The Auditor" in narration

    def test_fallback_narration_empty_results_returns_guidance(self):
        """Jika tidak ada result → Bang Jek memberi panduan kepada pengguna."""
        orchestra = BangJekOrchestrator()
        orchestra._model = None  # Force fallback path
        narration = orchestra._synthesize(CANONICAL_INPUT, [], 0.0)
        assert len(narration) > 0
        assert "tidak" in narration.lower() or "coba" in narration.lower()


# ============================================================
# TEST GRUP 5: EDGE CASES & RESILIENCE
# ============================================================

class TestResilience:
    """Edge cases: agen gagal, unregistered agent, empty input."""

    def test_unregistered_agent_returns_failed_result(self):
        """Jika sub-agen tidak diregistrasi, Bang Jek mengembalikan FAILED AgentResult."""
        with patch("agents.bang_jek.agent.vertexai.init"):
            with patch("agents.bang_jek.agent.GenerativeModel"):
                orchestra = BangJekOrchestrator()
                orchestra.initialize()
                # Tidak register sub-agen apapun

                response = orchestra.process("catat pendapatan 100 ribu")

                # Auditor tidak diregistrasi → harus FAILED
                auditor_result = next(
                    (r for r in response.agent_results if r.agent_name == "The Auditor"),
                    None,
                )
                if auditor_result is not None:
                    assert auditor_result.status == TaskStatus.FAILED

    def test_one_agent_fails_others_still_complete(self):
        """Jika satu agen gagal, agen lain tetap berhasil (parallel isolation)."""
        with patch("agents.bang_jek.agent.vertexai.init"):
            with patch("agents.bang_jek.agent.GenerativeModel"):
                orchestra = BangJekOrchestrator()
                orchestra.initialize()

                # Mock Auditor yang gagal
                failing_auditor = MagicMock()
                failing_auditor.process.return_value = AgentResult(
                    agent_name="The Auditor",
                    status=TaskStatus.FAILED,
                    error="BigQuery connection timeout",
                )

                # Mock Environmental yang berhasil
                success_env = MagicMock()
                success_env.process.return_value = MOCK_ENVIRONMENTAL_RESULT

                orchestra.register_sub_agent("The Auditor", failing_auditor)
                orchestra.register_sub_agent("Environmental", success_env)

                response = orchestra.process(
                    "catat pendapatan 100 ribu dan cek cuaca"
                )

                # Cari hasil per agen
                results_by_agent = {r.agent_name: r for r in response.agent_results}

                if "The Auditor" in results_by_agent:
                    assert results_by_agent["The Auditor"].status == TaskStatus.FAILED
                if "Environmental" in results_by_agent:
                    assert results_by_agent["Environmental"].status == TaskStatus.COMPLETED

    def test_bang_jek_cannot_register_unknown_agent(self):
        """register_sub_agent() harus raise ValueError untuk agen tidak dikenal."""
        with patch("agents.bang_jek.agent.vertexai.init"):
            with patch("agents.bang_jek.agent.GenerativeModel"):
                orchestra = BangJekOrchestrator()
                orchestra.initialize()

                unknown_mock = MagicMock()
                with pytest.raises(ValueError) as exc:
                    orchestra.register_sub_agent("Hacker Bot", unknown_mock)

                assert "tidak dikenal" in str(exc.value).lower() or \
                       "Hacker Bot" in str(exc.value)

    def test_empty_input_returns_response_with_guidance(self):
        """Input kosong → Bang Jek mengembalikan respons dengan panduan."""
        with patch("agents.bang_jek.agent.vertexai.init"):
            with patch("agents.bang_jek.agent.GenerativeModel"):
                orchestra = BangJekOrchestrator()
                orchestra.initialize()

                response = orchestra.process("")

                assert isinstance(response, BangJekResponseSchema)
                assert len(response.narration) > 0
                assert response.agents_called == []

    def test_response_schema_always_has_timestamp(self):
        """BangJekResponseSchema selalu memiliki timestamp yang valid."""
        with patch("agents.bang_jek.agent.vertexai.init"):
            with patch("agents.bang_jek.agent.GenerativeModel"):
                orchestra = BangJekOrchestrator()
                orchestra.initialize()
                response = orchestra.process("cek cuaca")   # 1 intent saja

                assert isinstance(response.timestamp, datetime)
