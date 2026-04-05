"""
guardrails/base_hook.py
=======================
Layer 3 — Abstract BaseHook.

Semua event hook (PreToolUse, PostToolUse) harus mewarisi class ini.
Sesuai CLAUDE.md Seksi 3.3.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from shared.schemas import ToolCallSchema, ValidationResultSchema
from shared.logger import get_logger

logger = get_logger("base_hook")


class BaseHook(ABC):
    """
    Abstract base class untuk semua guardrail event hooks.

    Subclass WAJIB mengimplementasikan:
    - pre_tool_use(): Dipanggil SEBELUM eksekusi tool
    - post_tool_use(): Dipanggil SESUDAH eksekusi tool
    """

    hook_name: str = "base_hook"

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self._logger = get_logger(f"hook.{self.hook_name}.{agent_name}")

    @abstractmethod
    def pre_tool_use(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ValidationResultSchema:
        """
        Intercept sebelum tool dipanggil.

        Args:
            tool_name: Nama fungsi tool yang akan dipanggil.
            parameters: Parameter yang akan dikirim ke tool.
            context: Konteks session opsional.

        Returns:
            ValidationResultSchema — jika is_valid=False, tool TIDAK akan dieksekusi.

        Raises:
            Tidak boleh raise exception langsung — kembalikan ValidationResultSchema dengan is_valid=False.
        """
        ...

    @abstractmethod
    def post_tool_use(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        result: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Intercept setelah tool selesai dieksekusi.

        Args:
            tool_name: Nama fungsi tool yang sudah dipanggil.
            parameters: Parameter yang dikirim ke tool.
            result: Hasil mentah dari tool.
            context: Konteks session opsional.

        Returns:
            Result yang mungkin sudah dimodifikasi/divalidasi.
        """
        ...

    def _build_tool_call_schema(
        self, tool_name: str, parameters: Dict[str, Any]
    ) -> ToolCallSchema:
        """Helper: Buat ToolCallSchema untuk logging."""
        return ToolCallSchema(
            tool_name=tool_name,
            agent_name=self.agent_name,
            parameters=parameters,
        )

    def _log_pre(self, tool_name: str, parameters: Dict[str, Any]) -> None:
        self._logger.log_tool_call(tool_name=tool_name, agent_name=self.agent_name)

    def _log_post(self, tool_name: str, success: bool) -> None:
        status = "SUCCESS" if success else "FAILED"
        self._logger.log_agent_event(
            f"POST_TOOL_USE: {tool_name} [{status}]",
            agent_name=self.agent_name,
            tool_name=tool_name,
            success=success,
        )
