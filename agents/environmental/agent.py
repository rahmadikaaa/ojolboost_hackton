"""
agents/environmental/agent.py
================================
Layer 4 — Sub-Agent: Environmental (Weather Monitor).

Alur kerja:
1. Terima AgentDelegation dari Bang Jek
2. Ekstrak lokasi dari delegation.context
3. Jalankan PreToolUseHook → validasi tool yang akan dipanggil
4. Eksekusi tool OpenWeather API
5. Jalankan PostToolUseHook → validasi & sanitasi output
6. Kembalikan AgentResult (JSON terstruktur) ke Bang Jek

ATURAN (CLAUDE.md Seksi 1.3 & 6.2):
- Output HANYA data JSON terstruktur — BUKAN narasi bahasa natural.
- Hanya akses OpenWeather API — tidak ada BigQuery, Calendar, atau service lain.
- Tidak ada komunikasi langsung ke agen lain.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from agents.environmental.config import AGENT_NAME, DEFAULT_LOCATION
from agents.environmental import tools as env_tools
from guardrails.pre_tool_use import PreToolUseHook
from guardrails.post_tool_use import PostToolUseHook
from shared.logger import get_logger
from shared.schemas import (
    AgentDelegation,
    AgentResult,
    TaskStatus,
    WeatherResponseSchema,
)

logger = get_logger("environmental.agent")


class EnvironmentalAgent:
    """
    Sub-Agent: Environmental — Weather Monitor.

    Menerima delegasi dari Bang Jek, memanggil OpenWeather API,
    dan mengembalikan AgentResult berisi data JSON terstruktur.

    Setiap tool call diproteksi oleh Layer 3 Guardrails:
    - PreToolUseHook  : validasi izin tool sebelum eksekusi
    - PostToolUseHook : validasi & sanitasi output setelah eksekusi
    """

    def __init__(self) -> None:
        self._agent_name = AGENT_NAME
        self._pre_hook = PreToolUseHook(agent_name=AGENT_NAME)
        self._post_hook = PostToolUseHook(agent_name=AGENT_NAME)

        logger.log_agent_event(
            "EnvironmentalAgent instantiated",
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
            AgentResult dengan data WeatherResponseSchema (JSON) — BUKAN narasi.
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

        except (EnvironmentError, ValueError) as e:
            # Error konfigurasi / lokasi tidak ditemukan — non-retriable
            logger.error(
                f"[{AGENT_NAME}] Config/Location error: {e}",
                delegation_id=delegation.delegation_id,
            )
            return AgentResult(
                delegation_id=delegation.delegation_id,
                agent_name=AGENT_NAME,
                status=TaskStatus.FAILED,
                error=str(e),
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
        location = ctx.get("location", DEFAULT_LOCATION)

        # Bersihkan nama lokasi dari prefiks preposisi ("di Sudirman" → "Sudirman")
        location = self._normalize_location(location)

        task_lower = delegation.task.lower()

        # -- Intent: Prakiraan cuaca jangka pendek --
        if any(w in task_lower for w in ["prakiraan", "forecast", "nanti", "besok", "jam ke depan"]):
            hours = ctx.get("forecast_hours", 3)
            return self._call_tool(
                tool_name="get_weather_forecast",
                parameters={"location": location, "forecast_hours": hours},
                tool_fn=env_tools.get_weather_forecast,
                location=location,
                forecast_hours=hours,
            )

        # -- Default: kondisi cuaca saat ini (real-time) --
        return self._call_tool(
            tool_name="get_current_weather",
            parameters={"location": location},
            tool_fn=env_tools.get_current_weather,
            location=location,
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
            result_dict = raw_result.model_dump(mode="json")
        else:
            result_dict = raw_result if isinstance(raw_result, dict) else {"data": raw_result}

        # Pastikan enum values diserialisasi sebagai string
        result_dict = _serialize_enums(result_dict)

        # ---- LAYER 3: Post-validation ----
        validated = self._post_hook.post_tool_use(
            tool_name=tool_name,
            parameters=parameters,
            result=result_dict,
        )

        return validated or result_dict

    # ----------------------------------------------------------
    # UTILITIES
    # ----------------------------------------------------------

    @staticmethod
    def _normalize_location(location: str) -> str:
        """
        Bersihkan nama lokasi dari prefiks/sufiks tidak perlu.
        Contoh: "di Sudirman" → "Sudirman", "area Kemayoran" → "Kemayoran"
        """
        prefixes = ["di ", "ke ", "dari ", "area ", "zona ", "daerah ", "kawasan "]
        loc = location.strip()
        for prefix in prefixes:
            if loc.lower().startswith(prefix):
                loc = loc[len(prefix):].strip()
                break
        return loc or DEFAULT_LOCATION


def _serialize_enums(data: Any) -> Any:
    """Rekursif konversi enum ke nilai string untuk JSON serialization."""
    if hasattr(data, "value"):
        return data.value
    if isinstance(data, dict):
        return {k: _serialize_enums(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_serialize_enums(item) for item in data]
    return data
