"""
shared/logger.py
================
Layer 1 — Structured JSON Logging.

ATURAN (CLAUDE.md Seksi 3.4):
- Semua komponen sistem WAJIB menggunakan logger dari modul ini.
- Format log adalah JSON untuk kompatibilitas Cloud Logging di GCP.
- Tidak ada print() statement di kode produksi — gunakan logger.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional


class JsonFormatter(logging.Formatter):
    """
    Custom formatter yang menghasilkan log dalam format JSON.
    Kompatibel dengan Google Cloud Logging structured logs.
    """

    SEVERITY_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "severity": self.SEVERITY_MAP.get(record.levelno, "DEFAULT"),
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Tambahkan exception info jika ada
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Tambahkan extra fields (agent_name, tool_name, dll.)
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)

        return json.dumps(log_entry, ensure_ascii=False)


class MamsLogger(logging.Logger):
    """
    Logger custom untuk sistem MAMS.
    Mendukung structured fields untuk tracing multi-agent.
    """

    def _pack_kwargs(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Mengemas kwargs tambahan ke dalam dictionary 'extra' untuk JSON formatter."""
        standard_keys = {"exc_info", "stack_info", "stacklevel", "extra"}
        std_kwargs = {k: v for k, v in kwargs.items() if k in standard_keys}
        extra_fields = {k: v for k, v in kwargs.items() if k not in standard_keys}
        
        extra = kwargs.get("extra", {})
        if extra_fields:
            if "extra_fields" not in extra:
                extra["extra_fields"] = {}
            extra["extra_fields"].update(extra_fields)
        
        if extra:
            std_kwargs["extra"] = extra
        return std_kwargs

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if self.isEnabledFor(logging.DEBUG):
            self._log(logging.DEBUG, msg, args, **self._pack_kwargs(kwargs))

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if self.isEnabledFor(logging.INFO):
            self._log(logging.INFO, msg, args, **self._pack_kwargs(kwargs))

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if self.isEnabledFor(logging.WARNING):
            self._log(logging.WARNING, msg, args, **self._pack_kwargs(kwargs))

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if self.isEnabledFor(logging.ERROR):
            self._log(logging.ERROR, msg, args, **self._pack_kwargs(kwargs))

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        if self.isEnabledFor(logging.CRITICAL):
            self._log(logging.CRITICAL, msg, args, **self._pack_kwargs(kwargs))

    def bind(self, **kwargs: Any) -> "BoundLogger":
        """Buat logger dengan context fields yang terikat."""
        return BoundLogger(self, kwargs)

    def log_agent_event(
        self,
        event: str,
        agent_name: str,
        level: int = logging.INFO,
        **extra: Any,
    ) -> None:
        """Log event spesifik agen dengan fields terstruktur."""
        self._log(
            level,
            f"[{agent_name}] {event}",
            args=(),
            extra={"extra_fields": {"agent_name": agent_name, **extra}},
        )

    def log_tool_call(self, tool_name: str, agent_name: str, **extra: Any) -> None:
        """Log pemanggilan tool (digunakan oleh guardrails)."""
        self.log_agent_event(
            f"TOOL_CALL: {tool_name}",
            agent_name=agent_name,
            level=logging.INFO,
            tool_name=tool_name,
            **extra,
        )

    def log_guardrail_block(self, tool_name: str, agent_name: str, reason: str) -> None:
        """Log saat guardrail memblokir suatu operasi."""
        self.log_agent_event(
            f"GUARDRAIL_BLOCK: {tool_name}",
            agent_name=agent_name,
            level=logging.ERROR,
            tool_name=tool_name,
            block_reason=reason,
            was_blocked=True,
        )

    def log_delegation(self, from_agent: str, to_agent: str, task: str) -> None:
        """Log delegasi tugas dari Bang Jek ke sub-agen."""
        self.log_agent_event(
            f"DELEGATION: {from_agent} → {to_agent}",
            agent_name=from_agent,
            level=logging.INFO,
            delegation_target=to_agent,
            task_summary=task[:100],
        )


class BoundLogger:
    """Logger wrapper dengan fields yang sudah terikat."""

    def __init__(self, logger: MamsLogger, fields: Dict[str, Any]) -> None:
        self._logger = logger
        self._fields = fields

    def _log(self, level: int, msg: str, **kwargs: Any) -> None:
        extra = {"extra_fields": {**self._fields, **kwargs}}
        self._logger.log(level, msg, extra=extra)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, **kwargs)

    def critical(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, msg, **kwargs)


# ============================================================
# Factory & Registry
# ============================================================

logging.setLoggerClass(MamsLogger)

_loggers: Dict[str, MamsLogger] = {}

# Flag global — kalau True, logger pakai file handler bukan stdout
_console_suppressed: bool = False
_log_file_path: str = os.getenv("LOG_FILE_PATH", "ojolboost.log")


def suppress_console_logs(suppress: bool = True, log_file: Optional[str] = None) -> None:
    """
    Matikan output log ke terminal (stdout).
    Bisa dipanggil kapan saja — termasuk SETELAH beberapa logger sudah dibuat.
    Akan mereset semua StreamHandler yang sudah ada ke FileHandler.

    Args:
        suppress: True = log ke file saja, False = log ke stdout.
        log_file: Path file log (default: ojolboost.log).
    """
    global _console_suppressed, _log_file_path
    _console_suppressed = suppress
    if log_file:
        _log_file_path = log_file

    if not suppress:
        return

    # Patch logger yang sudah terlanjur dibuat (module-level get_logger calls)
    for logger in _loggers.values():
        handlers_to_remove = [
            h for h in logger.handlers if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        for h in handlers_to_remove:
            formatter = h.formatter
            logger.removeHandler(h)
            h.close()
            # Ganti dengan FileHandler
            file_handler = logging.FileHandler(_log_file_path, encoding="utf-8")
            if formatter:
                file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)


def get_logger(name: str) -> MamsLogger:
    """
    Factory function untuk mendapatkan logger terstruktur.

    Usage:
        from shared.logger import get_logger
        logger = get_logger("the_auditor")
        logger.info("Transaksi dicatat", amount=250000)
    """
    if name in _loggers:
        return _loggers[name]  # type: ignore

    logger = logging.getLogger(f"ojolboost.{name}")

    if not logger.handlers:
        # Tentukan output: file (untuk CLI interaktif) atau stdout (untuk Cloud Run / prod)
        log_output = os.getenv("LOG_OUTPUT", "").lower()
        use_file = _console_suppressed or log_output == "file"

        if use_file:
            handler = logging.FileHandler(_log_file_path, encoding="utf-8")
        else:
            handler = logging.StreamHandler(sys.stdout)

        log_format = os.getenv("LOG_FORMAT", "json").lower()
        if log_format == "json":
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(
                logging.Formatter(
                    "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s"
                )
            )

        logger.addHandler(handler)

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logger.propagate = False

    _loggers[name] = logger  # type: ignore
    return logger  # type: ignore
