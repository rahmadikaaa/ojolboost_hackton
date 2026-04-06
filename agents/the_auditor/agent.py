"""
agents/the_auditor/agent.py
==============================
Layer 4 — Sub-Agent: The Auditor (Finance Auditor & State Manager).

Alur kerja:
1. Terima AgentDelegation dari Bang Jek
2. Parse intent keuangan dari delegation.context
3. Jalankan PreToolUseHook → validasi tool yang akan dipanggil
4. Dispatch ke tool yang sesuai (sudah memiliki L3 gate internal via validator.py)
5. Jalankan PostToolUseHook → validasi & sanitasi output
6. Kembalikan AgentResult (JSON terstruktur) ke Bang Jek

JAMINAN KEAMANAN BERLAPIS:
Layer 3a: PreToolUseHook   — validasi SEBELUM _call_tool()
Layer 3b: verify_and_clean_query — validasi SETIAP SQL di dalam tools.py
Layer 3c: PostToolUseHook  — validasi output SETELAH tool selesai

Ini berarti setiap query BigQuery melewati DUA lapisan keamanan independen.

ATURAN (CLAUDE.md Seksi 1.3 & 6.5):
- Output HANYA data JSON terstruktur — BUKAN narasi bahasa natural.
- Semua query divalidasi sebelum dieksekusi (tidak ada pengecualian).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agents.the_auditor.config import AGENT_NAME
from agents.the_auditor import tools as auditor_tools
from agents.the_auditor.validator import validate_transaction_amount, validate_service_type
from guardrails.pre_tool_use import PreToolUseHook
from guardrails.post_tool_use import PostToolUseHook
from shared.logger import get_logger
from shared.schemas import (
    AgentDelegation,
    AgentResult,
    ServiceType,
    TaskStatus,
    TransactionSchema,
)

logger = get_logger("the_auditor.agent")


class TheAuditorAgent:
    """
    Sub-Agent: The Auditor — Finance Auditor & State Manager.

    Menerima delegasi dari Bang Jek, mengelola data keuangan melalui
    BigQuery (READ + terbatas WRITE), dan mengembalikan AgentResult
    berisi data JSON terstruktur.

    Keamanan berlapis:
      [PreToolUseHook] → [_call_tool()] → [verify_and_clean_query()] → [BQ Client]
                                              ↑ tools.py memanggil ini
                                          [PostToolUseHook] ← [result]
    """

    def __init__(self) -> None:
        self._agent_name = AGENT_NAME
        self._pre_hook = PreToolUseHook(agent_name=AGENT_NAME)
        self._post_hook = PostToolUseHook(agent_name=AGENT_NAME)

        logger.log_agent_event(
            "TheAuditorAgent instantiated",
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
            AgentResult dengan data keuangan (JSON) — BUKAN narasi.
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

        except PermissionError as e:
            # Hard block dari AuditorValidator atau PreToolUseHook
            logger.error(
                f"[{AGENT_NAME}] GUARDRAIL HARD BLOCK: {e}",
                delegation_id=delegation.delegation_id,
            )
            return AgentResult(
                delegation_id=delegation.delegation_id,
                agent_name=AGENT_NAME,
                status=TaskStatus.BLOCKED,
                error=str(e),
                execution_time_ms=round((time.time() - start) * 1000, 2),
            )

        except ValueError as e:
            # Validasi data (amount tidak valid, service type tidak dikenal)
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
        Semua path WAJIB melalui _call_tool() yang terintegrasi guardrail.
        """
        ctx = delegation.context or {}
        intent = ctx.get("intent_type", "")
        task_lower = delegation.task.lower()
        driver_id = ctx.get("driver_id", "default_driver")

        # --- Intent: Catat transaksi baru ---
        if intent == "record_transaction" or any(
            w in task_lower for w in ["catat", "record", "insert", "transaksi baru"]
        ):
            return self._handle_record_transaction(ctx, driver_id)

        # --- Intent: Target Reverse Calc (Target Hunter) ---
        if intent == "target_reverse_calc" or any(
            w in task_lower for w in ["kejar target", "hitung bersih", "kalkulasi"]
        ):
            return self._handle_target_hunter(ctx, driver_id)

        # --- Intent: Laporan keuangan ---
        if intent == "get_financial_report" or any(
            w in task_lower for w in ["laporan", "rekap", "report", "summary", "berapa"]
        ):
            period = ctx.get("report_period", "daily")
            return self._call_tool(
                tool_name="get_financial_report",
                parameters={"period": period, "driver_id": driver_id},
                tool_fn=auditor_tools.get_financial_report,
                period=period,
                driver_id=driver_id if driver_id != "default_driver" else None,
            )

        # --- Intent: Snapshot real-time ---
        if intent == "get_daily_state" or any(
            w in task_lower for w in ["state", "snapshot", "saldo sekarang", "status hari ini"]
        ):
            return self._call_tool(
                tool_name="get_daily_state",
                parameters={"driver_id": driver_id},
                tool_fn=auditor_tools.get_daily_state,
                driver_id=driver_id,
            )

        # --- Default: snapshot harian jika intent tidak jelas ---
        return self._call_tool(
            tool_name="get_financial_report",
            parameters={"period": "snapshot", "driver_id": driver_id},
            tool_fn=auditor_tools.get_financial_report,
            period="snapshot",
            driver_id=driver_id if driver_id != "default_driver" else None,
        )

    def _handle_record_transaction(
        self,
        ctx: Dict[str, Any],
        driver_id: str,
    ) -> Dict[str, Any]:
        """
        Bangun TransactionSchema dari context dan delegasikan ke record_transaction tool.
        Menggunakan TransactionSchema dari shared/schemas.py.
        """
        # Ekstrak amount
        raw_amount = ctx.get("amount")
        if raw_amount is None:
            raise ValueError(
                "Amount transaksi tidak ditemukan dalam context delegasi. "
                "Pastikan IntentAnalyzer berhasil mengekstrak amount dari input pengguna."
            )

        amount = float(raw_amount)

        # Ekstrak dan validasi service type
        raw_service = ctx.get("service_type", "ride")
        service_type_str = validate_service_type(str(raw_service))
        service_type_enum = ServiceType(service_type_str)

        # Bangun TransactionSchema (shared/schemas.py)
        transaction = TransactionSchema(
            amount=amount,
            service_type=service_type_enum,
            zone=ctx.get("zone"),
            notes=ctx.get("notes") or ctx.get("raw_input", "")[:500],
            driver_id=driver_id,
            transaction_date=datetime.now(tz=timezone.utc),
        )

        return self._call_tool(
            tool_name="record_transaction",
            parameters={
                "amount": amount,
                "service_type": service_type_str,
                "driver_id": driver_id,
            },
            tool_fn=auditor_tools.record_transaction,
            transaction=transaction,
        )

    def _handle_target_hunter(
        self,
        ctx: Dict[str, Any],
        driver_id: str,
    ) -> Dict[str, Any]:
        """Layer 3-5: Cost Engine, Calculator, & Validation for Target Hunter."""
        import math
        
        target_amount = float(ctx.get("amount") or 0)
        if target_amount <= 0:
            raise ValueError("Target amount tidak valid untuk kalkulasi target hunter.")
            
        calc_mode = ctx.get("calc_mode", "GROSS")
        raw_costs = ctx.get("costs", [])
        
        # Panggil tool untuk Layer 2 (Data Retrieval)
        # _call_tool memastikan penjagaan L3 guardrail
        medians = self._call_tool(
            tool_name="get_historical_medians",
            parameters={"driver_id": driver_id},
            tool_fn=auditor_tools.get_historical_medians,
            driver_id=driver_id
        )
        
        # Layer 3: Cost Engine
        fixed_cost = 0.0
        for cost in raw_costs:
            if cost["type"] == "fixed":
                if cost["item"] == "rokok":
                    fixed_cost += 20000.0
                elif cost["item"] == "kopi":
                    fixed_cost += 8000.0
                else:
                    fixed_cost += 10000.0
                    
        has_bensin = any(c["item"] == "bensin" for c in raw_costs)
        base_variable_cost = medians["komisi_per_trip"]
        if has_bensin:
            base_variable_cost += 1600.0 # estimasi bensin per trip
            
        # Layer 4: The Calculator
        if calc_mode == "NET":
            total_needed = target_amount + fixed_cost
        else:
            total_needed = target_amount
            
        avg_argo = medians["avg_argo"]
        net_per_trip = avg_argo - base_variable_cost
        
        trip_minimum = math.ceil(total_needed / net_per_trip)
        cancel_rate = medians["cancel_rate"]
        real_orders_needed = math.ceil(trip_minimum / (1 - cancel_rate))
        
        # Estimasi waktu
        total_time_mins = real_orders_needed * medians.get("avg_cycle_time", 35)
        buffer_time_mins = total_time_mins * 1.15
        
        # Layer 5: Validation
        historic_daily_max = medians["avg_trips_per_day"] * 1.8 
        if real_orders_needed > max(historic_daily_max, 12):
            feasibility = "ABOVE NORMAL"
        elif buffer_time_mins > 540: # > 9 hours
            feasibility = "TIGHT"
        else:
            feasibility = "FEASIBLE"
            
        logger.info(f"[The Auditor] Target Hunter kalkulasi selesai: {trip_minimum} trips needed.")
            
        return {
            "intent": "TARGET_REVERSE_CALC",
            "target_amount": target_amount,
            "calc_mode": calc_mode,
            "cost_breakdown": {
                "fixed_cost_total": fixed_cost,
                "variable_cost_per_trip": base_variable_cost,
                "bensin_included": has_bensin
            },
            "math_result": {
                "minimum_trips": trip_minimum,
                "orders_to_accept": real_orders_needed,
                "net_per_trip": net_per_trip,
                "total_needed": total_needed
            },
            "time_estimation": {
                "total_minutes": buffer_time_mins,
                "hours": int(buffer_time_mins // 60),
                "minutes": int(buffer_time_mins % 60)
            },
            "validation": {
                "feasibility": feasibility,
                "historic_daily_capacity": medians["avg_trips_per_day"]
            }
        }

    # ----------------------------------------------------------
    # GUARDRAIL-WRAPPED TOOL CALL
    # Layer 3a (PreToolUseHook) + Layer 3b (verify_and_clean_query di tools.py)
    # + Layer 3c (PostToolUseHook)
    # ----------------------------------------------------------

    def _call_tool(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        tool_fn,
        **tool_kwargs,
    ) -> Dict[str, Any]:
        """
        Wrapper dengan guardrail L3 untuk setiap tool call.

        Alur keamanan berlapis:
          PreToolUseHook  → [PASS] → tool_fn() [memanggil verify_and_clean_query()]
                                              → PostToolUseHook

        Catatan: tool_fn di The Auditor memanggil verify_and_clean_query() secara internal.
        Ini berarti ada DUA lapisan validasi yang independen:
          1. PreToolUseHook — validasi tool-level (nama tool, agent permission)
          2. verify_and_clean_query — validasi SQL-level (konten query, tabel, operasi)
        """
        # ---- LAYER 3a: Pre-validation (tool permission) ----
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
                f"[GUARDRAIL L3a BLOCKED] '{tool_name}' diblokir oleh PreToolUseHook: "
                f"{pre_result.errors}"
            )

        # ---- Eksekusi tool (tool memanggil L3b: verify_and_clean_query secara internal) ----
        logger.log_tool_call(tool_name=tool_name, agent_name=AGENT_NAME)
        raw_result = tool_fn(**tool_kwargs)

        # Normalisasi ke dict
        if hasattr(raw_result, "model_dump"):
            result_dict = raw_result.model_dump(mode="json")
        else:
            result_dict = raw_result if isinstance(raw_result, dict) else {"data": raw_result}

        # Konversi enum ke string
        if "status" in result_dict and hasattr(result_dict.get("status"), "value"):
            result_dict["status"] = result_dict["status"].value

        # ---- LAYER 3c: Post-validation ----
        validated = self._post_hook.post_tool_use(
            tool_name=tool_name,
            parameters=parameters,
            result=result_dict,
        )

        return validated or result_dict
