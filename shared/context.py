"""
shared/context.py
=================
Layer 1 — Session Context Manager.

ATURAN (CLAUDE.md Seksi 3.4 & 3.5):
- State percakapan disimpan secara stateless per request.
- Context tidak boleh persist antar session tanpa intent eksplisit.
- mcp_server/server.py harus menggunakan context ini, bukan menyimpan state sendiri.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from threading import local
from typing import Any, Dict, Generator, List, Optional

from shared.schemas import AgentDelegation, AgentResult, TaskStatus
from shared.logger import get_logger

logger = get_logger("context")

# ============================================================
# Session Data Classes
# ============================================================

@dataclass
class AgentExecutionRecord:
    """Record eksekusi satu agen dalam sebuah session."""
    delegation: AgentDelegation
    result: Optional[AgentResult] = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    @property
    def latency_ms(self) -> Optional[float]:
        if self.completed_at:
            delta = self.completed_at - self.started_at
            return delta.total_seconds() * 1000
        return None


@dataclass
class SessionContext:
    """
    Konteks sebuah session percakapan tunggal.
    Stateless — dibuat baru untuk setiap request masuk.
    """
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_input: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    executions: List[AgentExecutionRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # State tracking
    _is_complete: bool = False
    _final_narration: Optional[str] = None

    def record_delegation(self, delegation: AgentDelegation) -> AgentExecutionRecord:
        """Catat delegasi tugas ke sub-agen."""
        record = AgentExecutionRecord(delegation=delegation)
        self.executions.append(record)
        logger.log_delegation(
            from_agent="Bang Jek",
            to_agent=delegation.agent_name,
            task=delegation.task,
        )
        return record

    def record_result(self, delegation_id: str, result: AgentResult) -> None:
        """Catat hasil dari sub-agen."""
        for execution in self.executions:
            if execution.delegation.delegation_id == delegation_id:
                execution.result = result
                execution.completed_at = datetime.utcnow()
                logger.log_agent_event(
                    f"RESULT_RECEIVED: status={result.status}",
                    agent_name=result.agent_name,
                    latency_ms=execution.latency_ms,
                )
                return
        logger.warning(f"Delegation ID {delegation_id} tidak ditemukan dalam context.")

    def get_all_results(self) -> List[AgentResult]:
        """Dapatkan semua hasil yang sudah selesai."""
        return [
            e.result for e in self.executions
            if e.result is not None
        ]

    def get_failed_agents(self) -> List[str]:
        """Dapatkan daftar agen yang gagal."""
        return [
            e.result.agent_name
            for e in self.executions
            if e.result and e.result.status == TaskStatus.FAILED
        ]

    @property
    def total_latency_ms(self) -> float:
        """Hitung total latency dari semua eksekusi selesai."""
        if not self.executions:
            return 0.0
        completed = [e for e in self.executions if e.completed_at]
        if not completed:
            return 0.0
        start = min(e.started_at for e in completed)
        end = max(e.completed_at for e in completed)  # type: ignore
        return (end - start).total_seconds() * 1000

    @property
    def agents_called(self) -> List[str]:
        """Daftar nama agen yang dipanggil dalam session ini."""
        return [e.delegation.agent_name for e in self.executions]

    def finalize(self, narration: str) -> None:
        """Tandai session sebagai selesai dengan narasi akhir."""
        self._is_complete = True
        self._final_narration = narration

    def set_metadata(self, key: str, value: Any) -> None:
        """Simpan metadata tambahan (non-sensitive)."""
        self.metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        return self.metadata.get(key, default)


# ============================================================
# Thread-local Context Registry
# Memungkinkan akses context dari mana saja dalam satu thread
# tanpa passing eksplisit (aman untuk Cloud Run single-runtime)
# ============================================================

_thread_local = local()


def _get_registry() -> Dict[str, SessionContext]:
    if not hasattr(_thread_local, "registry"):
        _thread_local.registry = {}
    return _thread_local.registry


def create_session(user_input: str) -> SessionContext:
    """Buat session context baru untuk setiap request pengguna."""
    ctx = SessionContext(user_input=user_input)
    _get_registry()[ctx.session_id] = ctx
    logger.info(f"Session created: {ctx.session_id}")
    return ctx


def get_session(session_id: str) -> Optional[SessionContext]:
    """Ambil session context berdasarkan ID."""
    return _get_registry().get(session_id)


def close_session(session_id: str) -> None:
    """Hapus session context setelah request selesai (stateless cleanup)."""
    registry = _get_registry()
    if session_id in registry:
        del registry[session_id]
        logger.info(f"Session closed: {session_id}")


@contextmanager
def session_scope(user_input: str) -> Generator[SessionContext, None, None]:
    """
    Context manager untuk mengelola lifecycle session.

    Usage:
        with session_scope("Bang Jek, catat pendapatan 250 ribu") as ctx:
            # Delegasi tugas, kumpulkan hasil
            ...
        # Session otomatis ditutup setelah block selesai
    """
    ctx = create_session(user_input)
    try:
        yield ctx
    except Exception as e:
        logger.error(f"Session {ctx.session_id} error: {e}")
        raise
    finally:
        close_session(ctx.session_id)
