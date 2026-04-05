"""
agents/the_planner/agent.py
==============================
Layer 4 — Sub-Agent: The Planner (Operations Manager).

Alur kerja:
1. Terima AgentDelegation dari Bang Jek
2. Parse intent & temporal context dari delegation.context
3. Jalankan PreToolUseHook → validasi tool yang akan dipanggil
4. Eksekusi tool MCP yang sesuai (dengan conflict check wajib)
5. Jalankan PostToolUseHook → validasi output
6. Kembalikan AgentResult (JSON terstruktur) ke Bang Jek

ATURAN (CLAUDE.md Seksi 1.3 & 6.3):
- Output HANYA data JSON terstruktur — BUKAN narasi bahasa natural.
- Hanya akses MCP Calendar & Task Manager.
- Wajib cek konflik jadwal sebelum membuat event apapun.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from agents.the_planner.config import AGENT_NAME, CALENDAR_TIMEZONE
from agents.the_planner import tools as planner_tools
from agents.the_planner.tools import _parse_datetime_hint
from guardrails.pre_tool_use import PreToolUseHook
from guardrails.post_tool_use import PostToolUseHook
from shared.logger import get_logger
from shared.schemas import (
    AgentDelegation,
    AgentResult,
    ScheduleEntrySchema,
    TaskStatus,
)

logger = get_logger("the_planner.agent")
WIB = ZoneInfo(CALENDAR_TIMEZONE)


class ThePlannerAgent:
    """
    Sub-Agent: The Planner — Operations Manager.

    Menerima delegasi dari Bang Jek, mengelola jadwal via MCP Calendar & Task Manager,
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
            "ThePlannerAgent instantiated",
            agent_name=AGENT_NAME,
        )

    # ----------------------------------------------------------
    # ENTRY POINT — dipanggil oleh BangJekOrchestrator
    # ----------------------------------------------------------

    def process(self, delegation: AgentDelegation) -> AgentResult:
        """
        Entry point untuk delegasi dari Bang Jek.

        Args:
            delegation: AgentDelegation berisi task & context.

        Returns:
            AgentResult dengan data PlannerResultSchema (JSON) — BUKAN narasi.
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
                f"[{AGENT_NAME}] ERROR: {e}",
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
    # DISPATCHER
    # ----------------------------------------------------------

    def _dispatch(self, delegation: AgentDelegation) -> Dict[str, Any]:
        """
        Tentukan tool yang dipanggil dan buat ScheduleEntrySchema.
        Semua path WAJIB melalui _call_tool().
        """
        ctx = delegation.context or {}
        task_lower = delegation.task.lower()
        raw_input = ctx.get("original_input", delegation.task)
        datetime_hint = ctx.get("datetime_hint", "")

        # --- Parse waktu ---
        if datetime_hint:
            scheduled_at = _parse_datetime_hint(datetime_hint)
        else:
            # Fallback: besok jam 09:00 WIB jika tidak ada hint
            scheduled_at = (
                datetime.now(tz=WIB).replace(
                    hour=9, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)
            )

        # --- Ekstrak judul dari task ---
        title = self._extract_title(raw_input, delegation.task)

        # --- Intent: Lihat daftar event yang ada ---
        if any(w in task_lower for w in ["lihat jadwal", "cek jadwal", "list event", "ada apa"]):
            return self._call_tool(
                tool_name="list_upcoming_events",
                parameters={},
                tool_fn=planner_tools.list_upcoming_events,
            )

        # --- Intent: Task reminder (pengingat one-time, tanpa slot kalender panjang) ---
        if any(w in task_lower for w in ["ingetin", "reminder", "pengingat", "jangan lupa"]):
            return self._call_tool(
                tool_name="create_task_reminder",
                parameters={
                    "title": title,
                    "due_datetime": scheduled_at.isoformat(),
                    "raw_input": raw_input,
                },
                tool_fn=planner_tools.create_task_reminder,
                title=title,
                due_datetime=scheduled_at,
                raw_input=raw_input,
            )

        # --- Default: buat calendar event (jadwal dengan durasi) ---
        entry = ScheduleEntrySchema(
            title=title,
            scheduled_at=scheduled_at,
            duration_minutes=30,
            description=f"Dibuat oleh Bang Jek MAMS dari: {raw_input[:200]}",
            reminder_minutes_before=15,
        )

        return self._call_tool(
            tool_name="create_calendar_event",
            parameters={
                "title": entry.title,
                "scheduled_at": entry.scheduled_at.isoformat(),
                "duration_minutes": entry.duration_minutes,
            },
            tool_fn=planner_tools.create_calendar_event,
            entry=entry,
        )

    # ----------------------------------------------------------
    # GUARDRAIL-WRAPPED TOOL CALL
    # ----------------------------------------------------------

    def _call_tool(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        tool_fn,
        **tool_kwargs,
    ) -> Dict[str, Any]:
        """
        Wrapper dengan guardrail L3 untuk setiap pemanggilan tool.

        Alur:
            PreToolUseHook → [PASS] → tool_fn() → PostToolUseHook
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
                f"[GUARDRAIL BLOCKED] '{tool_name}' diblokir: {pre_result.errors}"
            )

        # ---- Eksekusi tool ----
        logger.log_tool_call(tool_name=tool_name, agent_name=AGENT_NAME)
        raw_result = tool_fn(**tool_kwargs)

        # Normalisasi ke dict
        if hasattr(raw_result, "model_dump"):
            result_dict = raw_result.model_dump(mode="json")
        elif isinstance(raw_result, list):
            result_dict = {"events": raw_result, "count": len(raw_result)}
        else:
            result_dict = raw_result if isinstance(raw_result, dict) else {"data": raw_result}

        # Konversi TaskStatus enum ke string
        if "status" in result_dict and hasattr(result_dict["status"], "value"):
            result_dict["status"] = result_dict["status"].value

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
    def _extract_title(raw_input: str, task: str) -> str:
        """
        Ekstrak judul event dari input pengguna.
        Bersihkan prefiks perintah seperti "ingetin", "jadwalin", dll.
        """
        stop_words = [
            "ingetin", "ingatin", "reminder", "jadwalin", "jadwal",
            "buatin pengingat", "catat jadwal", "besok", "hari ini",
            "jam", "pagi", "siang", "sore", "malam", "buat",
        ]
        title = raw_input.strip()
        for word in stop_words:
            title = title.replace(word, "").strip()

        # Hapus sisa waktu yang tertinggal (contoh: "jam 9")
        import re
        title = re.sub(r'jam\s+\d{1,2}(?::\d{2})?', '', title).strip()
        title = re.sub(r'\s+', ' ', title).strip()

        # Fallback jika judul jadi kosong
        if not title or len(title) < 3:
            # Ambil dari task deskripsi Bang Jek
            task_clean = task.replace("Buat entri jadwal/pengingat berdasarkan:", "").strip()
            title = task_clean[:100] if task_clean else "Pengingat"

        return title[:100]   # Max 100 karakter
