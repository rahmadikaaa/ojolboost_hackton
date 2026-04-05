# agents/bang_jek/__init__.py
# Layer 4 — Primary Orchestrator Package
# ============================================================
# ATURAN #1 (CLAUDE.md Seksi 1.1):
# Bang Jek adalah SATU-SATUNYA entry point untuk semua
# permintaan pengguna. Ekspor HANYA BangJekOrchestrator.
# ============================================================

from agents.bang_jek.agent import BangJekOrchestrator

__all__ = ["BangJekOrchestrator"]
