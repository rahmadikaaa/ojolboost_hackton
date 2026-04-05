"""
tests/unit/test_guardrails.py
==============================
Unit tests untuk Layer 3 Guardrails:
  - guardrails/pre_tool_use.py  (PreToolUseHook)
  - guardrails/post_tool_use.py (PostToolUseHook)

Cakupan:
  - Validasi allowlist tool per agen
  - Hard block jika Bang Jek memanggil tool langsung (RULE #1)
  - Block jika agen tidak dikenal
  - Block jika tool bukan dalam allowlist agen
  - Post-hook: deteksi output None
  - Post-hook: redaksi field sensitif (password, token, api_key)
  - Post-hook: passthrough output valid

Semua test berjalan tanpa koneksi BigQuery, API eksternal, atau I/O.
"""

import pytest

from guardrails.pre_tool_use import PreToolUseHook
from guardrails.post_tool_use import PostToolUseHook
from shared.schemas import ValidationResultSchema


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture
def pre_hook_demand():
    """PreToolUseHook untuk Demand Analytics."""
    return PreToolUseHook(agent_name="Demand Analytics")


@pytest.fixture
def pre_hook_environmental():
    """PreToolUseHook untuk Environmental."""
    return PreToolUseHook(agent_name="Environmental")


@pytest.fixture
def pre_hook_planner():
    """PreToolUseHook untuk The Planner."""
    return PreToolUseHook(agent_name="The Planner")


@pytest.fixture
def pre_hook_archivist():
    """PreToolUseHook untuk The Archivist."""
    return PreToolUseHook(agent_name="The Archivist")


@pytest.fixture
def pre_hook_auditor():
    """PreToolUseHook untuk The Auditor."""
    return PreToolUseHook(agent_name="The Auditor")


@pytest.fixture
def pre_hook_bang_jek():
    """PreToolUseHook untuk Bang Jek (orchestrator)."""
    return PreToolUseHook(agent_name="Bang Jek")


@pytest.fixture
def pre_hook_unknown():
    """PreToolUseHook untuk agen yang tidak dikenal."""
    return PreToolUseHook(agent_name="Hacker Agent X")


@pytest.fixture
def post_hook():
    """PostToolUseHook untuk agen manapun (tidak agen-spesifik)."""
    return PostToolUseHook(agent_name="Demand Analytics")


# ============================================================
# PRE TOOL USE — BANG JEK RULE #1
# ============================================================

class TestPreToolUseBangJek:
    """Bang Jek tidak boleh memanggil tool apapun secara langsung."""

    def test_bang_jek_blocked_from_any_tool(self, pre_hook_bang_jek):
        """RULE #1: Bang Jek diblokir dari SEMUA tool call langsung."""
        result = pre_hook_bang_jek.pre_tool_use(
            tool_name="query_zone_demand",
            parameters={"start_hour": 8, "end_hour": 12},
        )
        assert result.is_valid is False
        assert any("Bang Jek" in e for e in result.errors)
        assert any("langsung" in e.lower() or "delegasi" in e.lower()
                   for e in result.errors)

    def test_bang_jek_blocked_from_bigquery_tool(self, pre_hook_bang_jek):
        """Bang Jek tidak boleh mengakses BigQuery langsung."""
        result = pre_hook_bang_jek.pre_tool_use(
            tool_name="insert_transaction",
            parameters={"amount": 50000},
        )
        assert result.is_valid is False

    def test_bang_jek_blocked_from_weather_tool(self, pre_hook_bang_jek):
        """Bang Jek tidak boleh mengakses OpenWeather langsung."""
        result = pre_hook_bang_jek.pre_tool_use(
            tool_name="get_current_weather",
            parameters={"location": "Jakarta"},
        )
        assert result.is_valid is False

    def test_bang_jek_blocked_from_calendar_tool(self, pre_hook_bang_jek):
        """Bang Jek tidak boleh mengakses Calendar langsung."""
        result = pre_hook_bang_jek.pre_tool_use(
            tool_name="create_calendar_event",
            parameters={"title": "Test"},
        )
        assert result.is_valid is False


# ============================================================
# PRE TOOL USE — UNKNOWN AGENT
# ============================================================

class TestPreToolUseUnknownAgent:
    """Agen yang tidak dikenal sistem harus diblokir."""

    def test_unknown_agent_is_blocked(self, pre_hook_unknown):
        """Agen tidak dikenal → is_valid=False."""
        result = pre_hook_unknown.pre_tool_use(
            tool_name="query_zone_demand",
            parameters={},
        )
        assert result.is_valid is False
        assert any("tidak dikenal" in e.lower() for e in result.errors)

    def test_unknown_agent_cannot_call_any_tool(self, pre_hook_unknown):
        """Agen tidak dikenal tidak bisa memanggil tool apapun."""
        for tool in ["get_current_weather", "save_note", "insert_transaction"]:
            result = pre_hook_unknown.pre_tool_use(
                tool_name=tool,
                parameters={},
            )
            assert result.is_valid is False, f"Tool '{tool}' seharusnya diblokir untuk agen tidak dikenal"


# ============================================================
# PRE TOOL USE — TOOL ALLOWLIST PER AGEN
# ============================================================

class TestPreToolUseAllowlist:
    """Setiap agen hanya boleh menggunakan tool yang ada di allowlist-nya."""

    # -- Demand Analytics --
    def test_demand_analytics_allowed_tool(self, pre_hook_demand):
        """query_zone_demand diizinkan untuk Demand Analytics."""
        result = pre_hook_demand.pre_tool_use(
            tool_name="query_zone_demand",
            parameters={"start_hour": 10, "end_hour": 14},
        )
        assert result.is_valid is True

    def test_demand_analytics_all_allowed_tools(self, pre_hook_demand):
        """Semua 3 tool Demand Analytics harus lolos validasi."""
        allowed = ["query_zone_demand", "query_historical_trends", "calculate_opportunity_cost"]
        for tool in allowed:
            result = pre_hook_demand.pre_tool_use(tool_name=tool, parameters={})
            assert result.is_valid is True, f"Tool '{tool}' seharusnya diizinkan"

    def test_demand_analytics_blocked_from_weather(self, pre_hook_demand):
        """Demand Analytics tidak boleh mengakses OpenWeather."""
        result = pre_hook_demand.pre_tool_use(
            tool_name="get_current_weather",
            parameters={"location": "Jakarta"},
        )
        assert result.is_valid is False
        assert any("tidak diizinkan" in e.lower() for e in result.errors)

    def test_demand_analytics_blocked_from_keep(self, pre_hook_demand):
        """Demand Analytics tidak boleh mengakses Google Keep."""
        result = pre_hook_demand.pre_tool_use(
            tool_name="save_note",
            parameters={"title": "test"},
        )
        assert result.is_valid is False

    # -- Environmental --
    def test_environmental_allowed_tools(self, pre_hook_environmental):
        """Semua 2 tool Environmental harus lolos validasi."""
        for tool in ["get_current_weather", "get_weather_forecast"]:
            result = pre_hook_environmental.pre_tool_use(tool_name=tool, parameters={})
            assert result.is_valid is True

    def test_environmental_blocked_from_bigquery(self, pre_hook_environmental):
        """Environmental tidak boleh mengakses BigQuery."""
        result = pre_hook_environmental.pre_tool_use(
            tool_name="query_zone_demand",
            parameters={},
        )
        assert result.is_valid is False

    # -- The Planner --
    def test_planner_allowed_tools(self, pre_hook_planner):
        """Semua 3 tool Planner harus lolos validasi."""
        for tool in ["create_calendar_event", "create_task_reminder", "list_upcoming_events"]:
            result = pre_hook_planner.pre_tool_use(tool_name=tool, parameters={})
            assert result.is_valid is True

    def test_planner_blocked_from_bigquery(self, pre_hook_planner):
        """The Planner tidak boleh mengakses BigQuery."""
        result = pre_hook_planner.pre_tool_use(
            tool_name="insert_transaction",
            parameters={},
        )
        assert result.is_valid is False

    # -- The Archivist --
    def test_archivist_allowed_tools(self, pre_hook_archivist):
        """Semua 3 tool Archivist harus lolos validasi."""
        for tool in ["save_note", "search_notes", "list_notes"]:
            result = pre_hook_archivist.pre_tool_use(tool_name=tool, parameters={})
            assert result.is_valid is True

    def test_archivist_blocked_from_calendar(self, pre_hook_archivist):
        """The Archivist tidak boleh mengakses Calendar."""
        result = pre_hook_archivist.pre_tool_use(
            tool_name="create_calendar_event",
            parameters={},
        )
        assert result.is_valid is False

    # -- The Auditor --
    def test_auditor_allowed_tools(self, pre_hook_auditor):
        """The Auditor tool harus lolos validasi."""
        for tool in ["insert_transaction", "query_financial_report", "get_daily_state"]:
            result = pre_hook_auditor.pre_tool_use(tool_name=tool, parameters={})
            assert result.is_valid is True

    def test_auditor_blocked_from_weather(self, pre_hook_auditor):
        """The Auditor tidak boleh mengakses OpenWeather."""
        result = pre_hook_auditor.pre_tool_use(
            tool_name="get_current_weather",
            parameters={},
        )
        assert result.is_valid is False

    def test_auditor_blocked_from_keep(self, pre_hook_auditor):
        """The Auditor tidak boleh mengakses Google Keep."""
        result = pre_hook_auditor.pre_tool_use(
            tool_name="save_note",
            parameters={},
        )
        assert result.is_valid is False


# ============================================================
# PRE TOOL USE — PARAMETER VALIDATION
# ============================================================

class TestPreToolUseParameters:
    """Validasi parameter hook."""

    def test_allowed_tool_with_none_params_returns_warning(self, pre_hook_demand):
        """Tool yang valid dengan parameter None mengembalikan is_valid=True tapi ada warning."""
        result = pre_hook_demand.pre_tool_use(
            tool_name="query_zone_demand",
            parameters=None,
        )
        # is_valid tetap True (hanya warning, bukan error)
        assert result.is_valid is True
        assert len(result.warnings) > 0

    def test_allowed_tool_with_empty_params_is_valid(self, pre_hook_demand):
        """Tool yang valid dengan parameter kosong (dict kosong) tetap valid."""
        result = pre_hook_demand.pre_tool_use(
            tool_name="query_zone_demand",
            parameters={},
        )
        assert result.is_valid is True

    def test_result_is_validation_result_schema(self, pre_hook_demand):
        """Return type harus ValidationResultSchema dari shared/schemas.py."""
        result = pre_hook_demand.pre_tool_use(
            tool_name="query_zone_demand",
            parameters={},
        )
        assert isinstance(result, ValidationResultSchema)


# ============================================================
# POST TOOL USE — OUTPUT VALIDATION & SANITIZATION
# ============================================================

class TestPostToolUse:
    """PostToolUseHook: validasi output, redaksi sensitif, passthrough."""

    def test_valid_dict_output_passes_through(self, post_hook):
        """Output dict yang valid harus dikembalikan apa adanya."""
        output = {"zone_name": "Sudirman", "probability": 0.75}
        result = post_hook.post_tool_use(
            tool_name="query_zone_demand",
            parameters={},
            result=output,
        )
        assert result == output
        assert result["zone_name"] == "Sudirman"

    def test_none_output_returns_none(self, post_hook):
        """Tool yang mengembalikan None harus tetap None (tidak crash)."""
        result = post_hook.post_tool_use(
            tool_name="query_zone_demand",
            parameters={},
            result=None,
        )
        assert result is None

    def test_sensitive_field_password_is_redacted(self, post_hook):
        """Field 'password' harus di-redact menjadi '[REDACTED]'."""
        output = {
            "zone_name": "Kemayoran",
            "password": "mysecret123",
        }
        result = post_hook.post_tool_use(
            tool_name="some_tool",
            parameters={},
            result=output,
        )
        assert result["password"] == "[REDACTED]"
        assert result["zone_name"] == "Kemayoran"   # Data valid tidak terpengaruh

    def test_sensitive_field_token_is_redacted(self, post_hook):
        """Field 'token' harus di-redact."""
        output = {"data": "ok", "token": "Bearer abc123"}
        result = post_hook.post_tool_use(
            tool_name="some_tool",
            parameters={},
            result=output,
        )
        assert result["token"] == "[REDACTED]"

    def test_sensitive_field_api_key_is_redacted(self, post_hook):
        """Field 'api_key' harus di-redact."""
        output = {"weather": "sunny", "api_key": "sk-xxxx"}
        result = post_hook.post_tool_use(
            tool_name="get_current_weather",
            parameters={},
            result=output,
        )
        assert result["api_key"] == "[REDACTED]"

    def test_multiple_sensitive_fields_all_redacted(self, post_hook):
        """Semua field sensitif dalam satu output harus di-redact sekaligus."""
        output = {
            "result": "ok",
            "password": "pass",
            "secret": "topsecret",
            "token": "tok",
        }
        result = post_hook.post_tool_use(
            tool_name="some_tool",
            parameters={},
            result=output,
        )
        assert result["password"] == "[REDACTED]"
        assert result["secret"] == "[REDACTED]"
        assert result["token"] == "[REDACTED]"
        assert result["result"] == "ok"

    def test_output_with_error_field_still_returned(self, post_hook):
        """Output dengan field 'error' tetap dikembalikan (tidak dibuang)."""
        output = {"error": "Koneksi timeout", "status": "failed"}
        result = post_hook.post_tool_use(
            tool_name="query_zone_demand",
            parameters={},
            result=output,
        )
        assert result is not None
        assert result["error"] == "Koneksi timeout"

    def test_non_dict_output_passes_through(self, post_hook):
        """Output berupa list tetap dikembalikan tanpa modifikasi."""
        output = [{"zone": "A"}, {"zone": "B"}]
        result = post_hook.post_tool_use(
            tool_name="query_zone_demand",
            parameters={},
            result=output,
        )
        assert result == output

    def test_non_serializable_output_still_returned(self, post_hook):
        """Output yang tidak sepenuhnya JSON-serializable tetap dikembalikan (warning saja)."""
        from datetime import datetime
        output = {"created_at": datetime.now()}   # datetime tidak JSON-serializable by default
        result = post_hook.post_tool_use(
            tool_name="query_zone_demand",
            parameters={},
            result=output,
        )
        # Harus tetap dikembalikan — bukan di-raise atau di-None-kan
        assert result is not None
        assert "created_at" in result

    def test_post_hook_pre_tool_use_always_valid(self, post_hook):
        """PostToolUseHook.pre_tool_use() selalu mengembalikan is_valid=True."""
        result = post_hook.pre_tool_use(
            tool_name="any_tool",
            parameters={},
        )
        assert result.is_valid is True


# ============================================================
# INTEGRATION: Pre + Post hook chain
# ============================================================

class TestHookChain:
    """Simulasi alur Pre → Eksekusi → Post seperti pada _call_tool() di agen."""

    def test_valid_chain_demand_analytics(self):
        """Simulasi full hook chain untuk Demand Analytics query yang valid."""
        pre = PreToolUseHook(agent_name="Demand Analytics")
        post = PostToolUseHook(agent_name="Demand Analytics")

        # Pre validation
        pre_result = pre.pre_tool_use(
            tool_name="query_zone_demand",
            parameters={"start_hour": 8, "end_hour": 12},
        )
        assert pre_result.is_valid is True

        # Simulasi tool execution result
        mock_tool_output = {
            "zones": [{"zone_name": "Sudirman", "probability_score": 0.78}],
            "confidence": 0.9,
        }

        # Post validation
        post_result = post.post_tool_use(
            tool_name="query_zone_demand",
            parameters={"start_hour": 8, "end_hour": 12},
            result=mock_tool_output,
        )
        assert post_result == mock_tool_output

    def test_blocked_chain_stops_at_pre(self):
        """Jika pre_tool_use gagal, hook chain berhenti — post tidak pernah dipanggil."""
        pre = PreToolUseHook(agent_name="Environmental")

        # Environmental mencoba akses BigQuery — harus diblokir di pre
        pre_result = pre.pre_tool_use(
            tool_name="query_zone_demand",
            parameters={},
        )
        assert pre_result.is_valid is False
        # Post tidak dipanggil jika pre gagal (ini adalah perilaku di agent._call_tool())
        # Test ini memverifikasi bahwa pre_result.is_valid=False bisa digunakan sebagai gate
        assert len(pre_result.errors) > 0
