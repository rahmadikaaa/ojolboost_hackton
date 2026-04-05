"""
agents/the_archivist/agent.py
================================
Layer 4 — Sub-Agent: The Archivist (Knowledge Base).

Alur kerja:
1. Terima AgentDelegation dari Bang Jek
2. Parse intent (simpan vs cari) dari delegation.context
3. Jalankan PreToolUseHook → validasi tool yang akan dipanggil
4. Eksekusi tool MCP yang sesuai (save/search/list)
5. Jalankan PostToolUseHook → validasi & sanitasi output
6. Kembalikan AgentResult (JSON terstruktur) ke Bang Jek

ATURAN (CLAUDE.md Seksi 1.3 & 6.4):
- Output HANYA data JSON terstruktur — BUKAN narasi bahasa natural.
- Hanya akses MCP Google Notes / Keep.
- Tidak ada operasi DELETE.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from agents.the_archivist.config import AGENT_NAME
from agents.the_archivist import tools as archivist_tools
from guardrails.pre_tool_use import PreToolUseHook
from guardrails.post_tool_use import PostToolUseHook
from shared.logger import get_logger
from shared.schemas import (
    AgentDelegation,
    AgentResult,
    TaskStatus,
)

logger = get_logger("the_archivist.agent")


class TheArchivistAgent:
    """
    Sub-Agent: The Archivist — Knowledge Base.

    Menerima delegasi dari Bang Jek, menyimpan/mencari catatan via
    MCP Google Keep/Notes, dan mengembalikan AgentResult berisi JSON terstruktur.

    Setiap tool call diproteksi oleh Layer 3 Guardrails:
    - PreToolUseHook  : validasi izin tool sebelum eksekusi
    - PostToolUseHook : validasi & sanitasi output setelah eksekusi
    """

    def __init__(self) -> None:
        self._agent_name = AGENT_NAME
        self._pre_hook = PreToolUseHook(agent_name=AGENT_NAME)
        self._post_hook = PostToolUseHook(agent_name=AGENT_NAME)

        logger.log_agent_event(
            "TheArchivistAgent instantiated",
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
            AgentResult dengan data catatan (JSON) — BUKAN narasi.
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

        except ValueError as e:
            # Validasi error (judul kosong, konten kosong) — non-retriable
            logger.error(
                f"[{AGENT_NAME}] Validation error: {e}",
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
        Tentukan tool yang dipanggil berdasarkan intent dari context.
        Semua path WAJIB melalui _call_tool().
        """
        ctx = delegation.context or {}
        intent = ctx.get("intent_type", "")
        task_lower = delegation.task.lower()
        raw_input = ctx.get("original_input", delegation.task)

        # --- Intent: Cari catatan (search_note) ---
        if intent == "search_note" or any(
            w in task_lower for w in ["cari catatan", "temuin", "ada catatan", "cari"]
        ):
            return self._handle_search(raw_input, ctx)

        # --- Intent: Daftar catatan terakhir ---
        if any(w in task_lower for w in ["lihat catatan", "list catatan", "catatan apa"]):
            return self._call_tool(
                tool_name="list_notes",
                parameters={"max_results": 10, "days_back": 7},
                tool_fn=archivist_tools.list_notes,
                max_results=10,
                days_back=7,
            )

        # --- Default: Simpan catatan baru (save_note) ---
        return self._handle_save(raw_input, ctx)

    def _handle_save(self, raw_input: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Persiapkan dan simpan catatan baru."""
        # Bersihkan kata perintah dari konten
        content = self._clean_save_command(raw_input)
        title = self._extract_note_title(content)
        user_tags: List[str] = ctx.get("tags", [])

        return self._call_tool(
            tool_name="save_note",
            parameters={"title": title, "content": content},
            tool_fn=archivist_tools.save_note,
            title=title,
            content=content,
            tags=user_tags,
        )

    def _handle_search(self, raw_input: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Persiapkan dan jalankan pencarian catatan."""
        # Ekstrak query dari input
        query = self._extract_search_query(raw_input)
        tags: Optional[List[str]] = ctx.get("filter_tags")
        days_back: Optional[int] = ctx.get("days_back")

        return self._call_tool(
            tool_name="search_notes",
            parameters={"query": query, "tags": tags, "days_back": days_back},
            tool_fn=archivist_tools.search_notes,
            query=query,
            tags=tags,
            days_back=days_back,
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
        else:
            result_dict = raw_result if isinstance(raw_result, dict) else {"data": raw_result}

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
    def _clean_save_command(raw_input: str) -> str:
        """Bersihkan kata perintah dari konten yang akan disimpan."""
        save_prefixes = [
            "catat ini", "simpan ini", "tulis ini",
            "catat catatan", "bikin catatan", "buat catatan",
            "save note", "note ini", "arsip",
            "catat", "simpan", "tulis",
        ]
        content = raw_input.strip()
        content_lower = content.lower()
        for prefix in sorted(save_prefixes, key=len, reverse=True):
            if content_lower.startswith(prefix):
                content = content[len(prefix):].strip(" :—-")
                break
        return content if content else raw_input

    @staticmethod
    def _extract_note_title(content: str) -> str:
        """
        Ekstrak judul singkat dari konten catatan.
        Ambil kalimat pertama atau 60 karakter pertama.
        """
        # Ambil kalimat pertama
        first_sentence = content.split(".")[0].split("\n")[0].strip()
        if len(first_sentence) <= 60:
            return first_sentence
        return first_sentence[:57] + "..."

    @staticmethod
    def _extract_search_query(raw_input: str) -> str:
        """Ekstrak query pencarian dari kalimat perintah."""
        search_prefixes = [
            "cari catatan soal", "cari catatan tentang", "cari catatan",
            "temuin catatan", "ada catatan soal", "ada catatan tentang",
            "tunjukin catatan", "catatan soal", "catatan tentang",
            "pernah catat", "apa yang dicatat tentang",
            "cari",
        ]
        query = raw_input.strip()
        query_lower = query.lower()
        for prefix in sorted(search_prefixes, key=len, reverse=True):
            if prefix in query_lower:
                idx = query_lower.index(prefix)
                query = query[idx + len(prefix):].strip(" :?")
                break
        return query[:200] if query else raw_input[:200]
