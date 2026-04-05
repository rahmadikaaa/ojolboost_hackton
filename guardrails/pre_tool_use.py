"""
guardrails/pre_tool_use.py
==========================
Layer 3 — PreToolUse Event Handler.

Diregistrasi di agent.py masing-masing agen.
Intercept SEMUA pemanggilan tool sebelum dieksekusi.
Sesuai CLAUDE.md Seksi 3.3.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from guardrails.base_hook import BaseHook
from shared.schemas import ValidationResultSchema
from shared.logger import get_logger

logger = get_logger("pre_tool_use")


class PreToolUseHook(BaseHook):
    """
    Event hook yang dijalankan SEBELUM setiap tool call.

    Fungsi:
    1. Log setiap pemanggilan tool untuk observability.
    2. Validasi bahwa agen hanya memanggil tool yang diizinkan.
    3. Validasi format parameter dasar sebelum eksekusi.
    """

    hook_name = "pre_tool_use"

    # Map agen → tools yang diizinkan
    AGENT_ALLOWED_TOOLS: Dict[str, set] = {
        "Bang Jek": set(),  # Bang Jek tidak boleh memanggil tool apapun langsung
        "Demand Analytics": {"query_zone_demand", "query_historical_trends", "calculate_opportunity_cost"},
        "Environmental": {"get_current_weather", "get_weather_forecast"},
        "The Planner": {"create_calendar_event", "create_task_reminder", "list_upcoming_events"},
        "The Archivist": {"save_note", "search_notes", "list_notes"},
        "The Auditor": {"insert_transaction", "query_financial_report", "get_daily_state"},
    }

    def pre_tool_use(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ValidationResultSchema:
        """Validasi sebelum tool dieksekusi."""
        errors = []
        warnings = []

        # Log pemanggilan
        self._log_pre(tool_name, parameters)

        # 1. Cek apakah agen dikenal
        if self.agent_name not in self.AGENT_ALLOWED_TOOLS:
            errors.append(f"Agen '{self.agent_name}' tidak dikenal dalam sistem.")
            logger.log_guardrail_block(
                tool_name=tool_name,
                agent_name=self.agent_name,
                reason=f"Unknown agent: {self.agent_name}",
            )
            return ValidationResultSchema(is_valid=False, errors=errors)

        # 2. Cek apakah Bang Jek mencoba memanggil tool langsung (PELANGGARAN ATURAN #1)
        if self.agent_name == "Bang Jek" and tool_name:
            errors.append(
                "PELANGGARAN ARSITEKTUR: Bang Jek tidak boleh memanggil tool secara langsung. "
                "Gunakan mekanisme delegasi ke sub-agen."
            )
            logger.log_guardrail_block(
                tool_name=tool_name,
                agent_name="Bang Jek",
                reason="Bang Jek attempted direct tool call (violates RULE #1)",
            )
            return ValidationResultSchema(is_valid=False, errors=errors)

        # 3. Cek apakah tool diizinkan untuk agen ini
        allowed_tools = self.AGENT_ALLOWED_TOOLS.get(self.agent_name, set())
        if allowed_tools and tool_name not in allowed_tools:
            errors.append(
                f"Tool '{tool_name}' tidak diizinkan untuk agen '{self.agent_name}'. "
                f"Tools yang diizinkan: {allowed_tools}"
            )
            logger.log_guardrail_block(
                tool_name=tool_name,
                agent_name=self.agent_name,
                reason=f"Tool not in allowlist for agent",
            )
            return ValidationResultSchema(is_valid=False, errors=errors)

        # 4. Validasi parameter tidak kosong
        if parameters is None:
            warnings.append(f"Tool '{tool_name}' dipanggil dengan parameter None.")

        result = ValidationResultSchema(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

        if warnings:
            for w in warnings:
                logger.warning(f"[PRE_TOOL_USE] {w}", agent=self.agent_name, tool=tool_name)

        return result

    def post_tool_use(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        result: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        PreToolUseHook tidak perlu melakukan post-processing.
        Delegasikan ke PostToolUseHook jika diperlukan.
        """
        self._log_post(tool_name, success=result is not None)
        return result
