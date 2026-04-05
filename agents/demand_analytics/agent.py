"""
agents/demand_analytics/agent.py
==================================
Layer 4 — Sub-Agent: Demand Analytics (Data Scientist).

Alur kerja:
1. Terima AgentDelegation dari Bang Jek
2. Parse intent dari delegation.context
3. Jalankan PreToolUseHook → validasi tool yang akan dipanggil
4. Eksekusi tool BigQuery READ-ONLY yang sesuai
5. Jalankan PostToolUseHook → validasi output
6. Kembalikan AgentResult (JSON terstruktur) ke Bang Jek

ATURAN (CLAUDE.md Seksi 1.3 & 6.1):
- Output HANYA data JSON terstruktur — bukan narasi bahasa natural.
- Tidak ada akses langsung ke agen lain.
- BigQuery hanya SELECT pada dataset ojolboosttrack2.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agents.demand_analytics.config import AGENT_NAME, ALLOWED_TOOLS
from agents.demand_analytics import tools as demand_tools
from guardrails.pre_tool_use import PreToolUseHook
from guardrails.post_tool_use import PostToolUseHook
from shared.logger import get_logger
from shared.schemas import (
    AgentDelegation,
    AgentResult,
    DemandAnalyticsResultSchema,
    TaskStatus,
)

logger = get_logger("demand_analytics.agent")


class DemandAnalyticsAgent:
    """
    Sub-Agent: Demand Analytics — Data Scientist.

    Menerima delegasi dari Bang Jek, mengeksekusi query BigQuery
    READ-ONLY, dan mengembalikan AgentResult berisi data JSON terstruktur.

    Setiap tool call diproteksi oleh Layer 3 Guardrails:
    - PreToolUseHook  : validasi sebelum query dieksekusi
    - PostToolUseHook : validasi & sanitasi setelah hasil diterima
    """

    def __init__(self) -> None:
        self._agent_name = AGENT_NAME
        self._pre_hook = PreToolUseHook(agent_name=AGENT_NAME)
        self._post_hook = PostToolUseHook(agent_name=AGENT_NAME)

        logger.log_agent_event(
            "DemandAnalyticsAgent instantiated",
            agent_name=AGENT_NAME,
        )

    # ----------------------------------------------------------
    # ENTRY POINT — dipanggil oleh BangJekOrchestrator
    # ----------------------------------------------------------

    def process(self, delegation: AgentDelegation) -> AgentResult:
        """
        Entry point untuk delegasi dari Bang Jek.

        Args:
            delegation: AgentDelegation dari Bang Jek berisi task & context.

        Returns:
            AgentResult dengan data JSON terstruktur — BUKAN narasi.
        """
        start = time.time()

        logger.log_agent_event(
            f"TASK_RECEIVED: '{delegation.task[:100]}'",
            agent_name=AGENT_NAME,
            delegation_id=delegation.delegation_id,
        )

        try:
            data = self._dispatch(delegation)
            return AgentResult(
                delegation_id=delegation.delegation_id,
                agent_name=AGENT_NAME,
                status=TaskStatus.COMPLETED,
                data=data,
                execution_time_ms=round((time.time() - start) * 1000, 2),
            )

        except Exception as e:
            logger.error(
                f"[{AGENT_NAME}] ERROR saat memproses delegasi: {e}",
                delegation_id=delegation.delegation_id,
            )
            return AgentResult(
                delegation_id=delegation.delegation_id,
                agent_name=AGENT_NAME,
                status=TaskStatus.FAILED,
                error=str(e),
                execution_time_ms=round((time.time() - start) * 1000, 2),
            )

    # ----------------------------------------------------------
    # DISPATCHER — routing ke tool yang tepat
    # ----------------------------------------------------------

    def _dispatch(self, delegation: AgentDelegation) -> Dict[str, Any]:
        """
        Tentukan tool mana yang dipanggil berdasarkan context delegasi.
        Semua path WAJIB melalui _call_tool() yang terintegrasi guardrail.
        """
        ctx = delegation.context or {}
        intent = ctx.get("intent_type", "")

        # -- Intent: Analisis demand zona (default) --
        if intent == "analyze_demand" or "hotzone" in delegation.task.lower() or not intent:
            return self._run_zone_demand_analysis(ctx)

        # -- Intent: Tren historis untuk zona tertentu --
        if "historical" in delegation.task.lower() or "tren" in delegation.task.lower():
            zone = ctx.get("zone_hint", ctx.get("zone", "Jakarta"))
            return self._call_tool(
                tool_name="query_historical_trends",
                parameters={"zone_name": zone},
                tool_fn=demand_tools.query_historical_trends,
                zone_name=zone,
            )

        # -- Intent: Kalkulasi opportunity cost --
        if "opportunity" in delegation.task.lower() or "rugi" in delegation.task.lower():
            zone = ctx.get("zone_hint", ctx.get("zone", "Jakarta"))
            return self._call_tool(
                tool_name="calculate_opportunity_cost",
                parameters={"current_zone": zone},
                tool_fn=demand_tools.calculate_opportunity_cost,
                current_zone=zone,
            )

        # -- Default: zone demand analysis --
        return self._run_zone_demand_analysis(ctx)

    def _run_zone_demand_analysis(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Jalankan analisis zona permintaan berdasarkan jam aktif saat ini."""
        now_hour = datetime.now(tz=timezone.utc).hour
        # Konversi ke WIB (UTC+7)
        wib_hour = (now_hour + 7) % 24

        return self._call_tool(
            tool_name="query_zone_demand",
            parameters={"start_hour": wib_hour, "end_hour": wib_hour},
            tool_fn=demand_tools.query_zone_demand,
            start_hour=wib_hour,
            end_hour=wib_hour,
        )

    # ----------------------------------------------------------
    # GUARDRAIL-WRAPPED TOOL CALL
    # Layer 3 wajib dijalankan untuk setiap tool call.
    # ----------------------------------------------------------

    def _call_tool(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        tool_fn,
        **tool_kwargs,
    ) -> Dict[str, Any]:
        """
        Wrapper untuk setiap pemanggilan tool dengan guardrail L3.

        Alur:
            PreToolUseHook.pre_tool_use() → [PASS] → tool_fn() → PostToolUseHook.post_tool_use()

        Raises:
            PermissionError: Jika PreToolUseHook memblokir tool call.
            RuntimeError: Jika tool gagal dieksekusi.
        """
        # ---- LAYER 3: Pre-validation ----
        pre_result = self._pre_hook.pre_tool_use(
            tool_name=tool_name,
            parameters=parameters,
        )

        if not pre_result.is_valid:
            logger.log_guardrail_block(
                tool_name=tool_name,
                agent_name=AGENT_NAME,
                reason="; ".join(pre_result.errors),
            )
            raise PermissionError(
                f"[GUARDRAIL BLOCKED] Tool '{tool_name}' diblokir: {pre_result.errors}"
            )

        # ---- Eksekusi tool ----
        logger.log_tool_call(tool_name=tool_name, agent_name=AGENT_NAME)

        raw_result = tool_fn(**tool_kwargs)

        # Normalisasi ke dict jika Pydantic model
        if hasattr(raw_result, "model_dump"):
            result_dict = raw_result.model_dump()
        else:
            result_dict = raw_result if isinstance(raw_result, dict) else {"data": raw_result}

        # ---- LAYER 3: Post-validation ----
        validated = self._post_hook.post_tool_use(
            tool_name=tool_name,
            parameters=parameters,
            result=result_dict,
        )

        return validated or result_dict
