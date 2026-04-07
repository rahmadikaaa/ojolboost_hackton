"""
agents/bang_jek/agent.py
========================
Layer 4 — Primary Orchestrator: BangJekOrchestrator.

ATURAN ABSOLUT (CLAUDE.md Seksi 1.1 & 1.3):
- Class ini adalah SATU-SATUNYA entry point untuk permintaan pengguna.
- DILARANG memanggil tool (BigQuery, OpenWeather, API) secara langsung.
- Hanya boleh: ANALYZE → DELEGATE → COLLECT RESULTS → SYNTHESIZE.
- Sintesis ke pengguna: Bahasa Indonesia, hangat, sapaan "Bang".
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import vertexai
from vertexai.generative_models import GenerativeModel

from agents.bang_jek.config import (
    AGENT_NAME,
    ALLOWED_DELEGATE_AGENTS,
    FALLBACK_MENU,
    GOOGLE_CLOUD_PROJECT,
    MODEL,
    SYSTEM_PROMPT,
    SYNTHESIS_PROMPT_TEMPLATE,
    VERTEX_AI_LOCATION,
)
from agents.bang_jek.router import ExecutionMode, IntentAnalyzer, TaskPlanner
from guardrails.pre_tool_use import PreToolUseHook
from shared.context import SessionContext, session_scope
from shared.logger import get_logger
from shared.schemas import (
    AgentDelegation,
    AgentResult,
    BangJekResponseSchema,
    TaskStatus,
)

logger = get_logger("bang_jek.agent")


# ============================================================
# HELPER: Fallback narasi jika Vertex AI tidak tersedia
# ============================================================

def _build_fallback_narration(
    user_input: str,
    results: List[AgentResult],
) -> str:
    """
    Narasi fallback deterministik jika Vertex AI tidak dapat dipanggil.
    Tetap dalam Bahasa Indonesia dan format sesuai CLAUDE.md Seksi 8.
    """
    parts: List[str] = ["Siap Bang! Ini hasilnya:"]

    for result in results:
        if result.status == TaskStatus.COMPLETED:
            agent = result.agent_name
            data = result.data

            if agent == "The Auditor":
                balance = data.get("balance_snapshot")
                if balance is not None:
                    parts.append(
                        f"✅ Pendapatan Rp {balance:,.0f} udah masuk buku catatan."
                    )
                else:
                    parts.append("✅ Data keuangan sudah diproses.")

            elif agent == "Environmental":
                condition = data.get("condition", "unknown")
                location = data.get("location", "area tujuan")
                pivot = data.get("pivot_recommendation", "")
                condition_text = {
                    "clear": "cerah ☀️",
                    "cloudy": "mendung ⛅",
                    "rain": "hujan 🌧️",
                    "heavy_rain": "hujan deras 🌧️⚠️",
                    "storm": "badai ⛈️",
                }.get(condition, condition)
                parts.append(f"🌤️ Cuaca {location}: {condition_text}.")
                if pivot:
                    parts.append(f"   → {pivot}")

            elif agent == "The Planner":
                title = data.get("title", "Pengingat")
                scheduled = data.get("scheduled_at", "")
                parts.append(f"✅ Jadwal '{title}' sudah disimpan — {scheduled}.")

            elif agent == "The Archivist":
                note_title = data.get("title", "Catatan baru")
                parts.append(f"✅ Catatan '{note_title}' sudah disimpan di Google Keep.")

            elif agent == "Demand Analytics":
                recommendation = data.get("recommendation", "")
                if recommendation:
                    parts.append(f"📊 {recommendation}")
                else:
                    parts.append("📊 Analisis permintaan selesai.")

        elif result.status == TaskStatus.FAILED:
            parts.append(
                f"⚠️ Ada kendala di {result.agent_name}: {result.error or 'error tidak diketahui'}. "
                f"Coba lagi ya Bang."
            )

    return " ".join(parts)


# ============================================================
# BANG JEK ORCHESTRATOR
# ============================================================

class BangJekOrchestrator:
    """
    Primary Orchestrator — Bang Jek.

    Alur kerja:
    1. process(user_input) → entry point
    2. IntentAnalyzer.analyze() → deteksi intent
    3. TaskPlanner.build_plan() → rencana eksekusi
    4. _execute_plan() → delegasi ke sub-agen (paralel/sekuensial)
    5. _synthesize() → narasi taktis Bahasa Indonesia
    6. Return BangJekResponseSchema

    TIDAK ADA tool call langsung di class ini.
    Sub-agen di-inject via register_sub_agent() untuk testability.
    """

    def __init__(self) -> None:
        self._agent_name = AGENT_NAME
        self._intent_analyzer = IntentAnalyzer()
        self._task_planner = TaskPlanner()
        self._pre_hook = PreToolUseHook(agent_name=AGENT_NAME)
        self._sub_agents: Dict[str, Any] = {}  # Diisi via register_sub_agent()
        self._model: Optional[GenerativeModel] = None
        self._initialized = False

        logger.log_agent_event(
            "BangJekOrchestrator instantiated",
            agent_name=AGENT_NAME,
        )

    # ----------------------------------------------------------
    # SETUP
    # ----------------------------------------------------------

    def initialize(self) -> None:
        """
        Inisialisasi Vertex AI. Dipanggil sekali saat container startup.
        Dipisah dari __init__ agar mudah di-mock saat testing.
        """
        if self._initialized:
            return
        try:
            vertexai.init(project=GOOGLE_CLOUD_PROJECT, location=VERTEX_AI_LOCATION)
            self._model = GenerativeModel(
                model_name=MODEL,
                system_instruction=SYSTEM_PROMPT,
            )
            self._initialized = True
            logger.log_agent_event(
                f"Vertex AI initialized: model={MODEL}, project={GOOGLE_CLOUD_PROJECT}",
                agent_name=AGENT_NAME,
            )
        except Exception as e:
            logger.error(
                f"[Bang Jek] Gagal inisialisasi Vertex AI: {e}. "
                f"Akan menggunakan fallback narasi deterministik."
            )
            self._model = None
            self._initialized = True  # Tetap tandai initialized agar tidak retry terus

    def register_sub_agent(self, agent_name: str, agent_instance: Any) -> None:
        """
        Daftarkan sub-agen ke registry orchestrator.

        Args:
            agent_name: Harus persis sesuai ALLOWED_DELEGATE_AGENTS.
            agent_instance: Instance sub-agen yang memiliki method process(delegation).

        Raises:
            ValueError: Jika agent_name tidak ada dalam daftar yang diizinkan.
        """
        if agent_name not in ALLOWED_DELEGATE_AGENTS:
            raise ValueError(
                f"Sub-agen '{agent_name}' tidak dikenal. "
                f"Agen yang diizinkan: {ALLOWED_DELEGATE_AGENTS}"
            )
        self._sub_agents[agent_name] = agent_instance
        logger.log_agent_event(
            f"Sub-agent registered: {agent_name}",
            agent_name=AGENT_NAME,
        )

    # ----------------------------------------------------------
    # ENTRY POINT UTAMA
    # ----------------------------------------------------------

    def process(self, user_input: str, driver_id: Optional[str] = None) -> BangJekResponseSchema:
        """
        Entry point untuk semua permintaan pengguna.

        Args:
            user_input: Pesan natural dari pengguna dalam Bahasa Indonesia.
            driver_id: ID pengemudi (opsional, untuk context BigQuery).

        Returns:
            BangJekResponseSchema — berisi narasi taktis + metadata semua eksekusi.
        """
        if not self._initialized:
            self.initialize()

        start_time = time.time()

        logger.log_agent_event(
            f"REQUEST_RECEIVED: '{user_input[:80]}...' " if len(user_input) > 80
            else f"REQUEST_RECEIVED: '{user_input}'",
            agent_name=AGENT_NAME,
            driver_id=driver_id,
        )

        with session_scope(user_input) as ctx:
            if driver_id:
                ctx.set_metadata("driver_id", driver_id)

            # --------------------------------------------------
            # STEP 1: Validasi guardrail — Bang Jek tidak boleh
            # memanggil tool langsung (enforce Rule #1)
            # --------------------------------------------------
            # PreToolUseHook sudah dikonfigurasi untuk memblokir
            # setiap tool call dari Bang Jek. Hook ini aktif untuk
            # sub-agen, bukan di sini — tapi tetap di-log sebagai
            # referensi bahwa orchestrator tidak memanggil tool.

            # --------------------------------------------------
            # STEP 2: Analisis intent
            # --------------------------------------------------
            intents = self._intent_analyzer.analyze(user_input)

            # --------------------------------------------------
            # STEP 3: Buat task plan
            # --------------------------------------------------
            task_plan = self._task_planner.build_plan(intents, user_input)
            ctx.set_metadata("task_plan_summary", task_plan.plan_summary)

            # --------------------------------------------------
            # STEP 4: Eksekusi delegasi
            # --------------------------------------------------
            results = self._execute_plan(task_plan, ctx)

            # --------------------------------------------------
            # STEP 5: Sintesis narasi
            # --------------------------------------------------
            total_ms = (time.time() - start_time) * 1000

            # Shortcut: jika tidak ada sub-agen yang dipanggil,
            # langsung tampilkan FALLBACK_MENU tanpa panggil Vertex AI
            if not results:
                narration = FALLBACK_MENU
            else:
                narration = self._synthesize(user_input, results, total_ms)


            # Periksa latency constraint (CLAUDE.md Seksi 7.4: < 5000ms)
            if total_ms > 5000:
                logger.warning(
                    f"[Bang Jek] LATENCY_WARNING: {total_ms:.0f}ms melebihi target 5000ms",
                    agent_name=AGENT_NAME,
                    latency_ms=total_ms,
                )

            response = BangJekResponseSchema(
                user_input=user_input,
                agent_results=results,
                narration=narration,
                total_latency_ms=round(total_ms, 2),
                agents_called=task_plan.delegations and
                              [d.agent_name for d in task_plan.delegations] or [],
            )

            logger.log_agent_event(
                f"REQUEST_COMPLETED: latency={total_ms:.0f}ms, "
                f"agents={response.agents_called}",
                agent_name=AGENT_NAME,
            )

            return response

    # ----------------------------------------------------------
    # EKSEKUSI DELEGASI
    # ----------------------------------------------------------

    def _execute_plan(
        self,
        task_plan,
        ctx: SessionContext,
    ) -> List[AgentResult]:
        """
        Eksekusi TaskPlan: delegasikan ke sub-agen sesuai execution mode.
        """
        if not task_plan.delegations:
            return []

        if task_plan.execution_mode == ExecutionMode.PARALLEL:
            return self._execute_parallel(task_plan.delegations, ctx)
        else:
            return self._execute_sequential(task_plan.delegations, ctx)

    def _execute_parallel(
        self,
        delegations: List[AgentDelegation],
        ctx: SessionContext,
    ) -> List[AgentResult]:
        """
        Eksekusi semua delegasi secara paralel menggunakan asyncio.
        Sesuai CLAUDE.md Seksi 1.2: delegasi paralel untuk tugas independen.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(
            self._run_parallel_delegations(delegations, ctx)
        )

    async def _run_parallel_delegations(
        self,
        delegations: List[AgentDelegation],
        ctx: SessionContext,
    ) -> List[AgentResult]:
        """Coroutine: jalankan semua delegasi secara concurrent."""
        tasks = [
            asyncio.create_task(self._delegate_single(delegation, ctx))
            for delegation in delegations
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Konversi exception ke AgentResult dengan status FAILED
        clean_results: List[AgentResult] = []
        for delegation, result in zip(delegations, results):
            if isinstance(result, Exception):
                logger.error(
                    f"[Bang Jek] Delegasi ke {delegation.agent_name} gagal: {result}"
                )
                clean_results.append(AgentResult(
                    delegation_id=delegation.delegation_id,
                    agent_name=delegation.agent_name,
                    status=TaskStatus.FAILED,
                    error=str(result),
                ))
            else:
                clean_results.append(result)

        return clean_results

    def _execute_sequential(
        self,
        delegations: List[AgentDelegation],
        ctx: SessionContext,
    ) -> List[AgentResult]:
        """
        Eksekusi delegasi satu per satu secara berurutan.
        Output setiap agen bisa menjadi context untuk agen berikutnya.
        """
        results: List[AgentResult] = []

        for delegation in delegations:
            # Enrich context dengan hasil dari agen sebelumnya
            if results:
                previous_data = {
                    r.agent_name: r.data
                    for r in results
                    if r.status == TaskStatus.COMPLETED
                }
                delegation.context = delegation.context or {}
                delegation.context["previous_results"] = previous_data

            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(
                self._delegate_single(delegation, ctx)
            )
            loop.close()
            results.append(result)

            # Jika satu agen gagal dalam mode sekuensial, hentikan chain
            if result.status == TaskStatus.FAILED:
                logger.error(
                    f"[Bang Jek] Sequential chain berhenti karena {delegation.agent_name} gagal."
                )
                break

        return results

    async def _delegate_single(
        self,
        delegation: AgentDelegation,
        ctx: SessionContext,
    ) -> AgentResult:
        """
        Delegasikan satu tugas ke sub-agen dan kembalikan hasilnya.

        Sesuai CLAUDE.md Seksi 1.3: "Results only" —
        sub-agen hanya mengembalikan data terstruktur, bukan keputusan akhir.
        """
        agent_name = delegation.agent_name
        start = time.time()

        logger.log_delegation(
            from_agent=AGENT_NAME,
            to_agent=agent_name,
            task=delegation.task,
        )

        ctx.record_delegation(delegation)

        # Cek apakah sub-agen sudah diregistrasi
        if agent_name not in self._sub_agents:
            logger.error(
                f"[Bang Jek] Sub-agen '{agent_name}' belum diregistrasi. "
                f"Panggil register_sub_agent() terlebih dahulu."
            )
            result = AgentResult(
                delegation_id=delegation.delegation_id,
                agent_name=agent_name,
                status=TaskStatus.FAILED,
                error=f"Sub-agen '{agent_name}' tidak tersedia dalam runtime ini.",
                execution_time_ms=round((time.time() - start) * 1000, 2),
            )
        else:
            try:
                sub_agent = self._sub_agents[agent_name]
                # Sub-agen WAJIB memiliki method process(delegation) → AgentResult
                raw_result = await asyncio.to_thread(sub_agent.process, delegation)

                if not isinstance(raw_result, AgentResult):
                    raise TypeError(
                        f"Sub-agen '{agent_name}' mengembalikan tipe yang tidak valid: "
                        f"{type(raw_result)}. Harus AgentResult."
                    )

                raw_result.execution_time_ms = round((time.time() - start) * 1000, 2)
                result = raw_result

            except Exception as e:
                logger.error(
                    f"[Bang Jek] Error dari sub-agen {agent_name}: {e}"
                )
                result = AgentResult(
                    delegation_id=delegation.delegation_id,
                    agent_name=agent_name,
                    status=TaskStatus.FAILED,
                    error=str(e),
                    execution_time_ms=round((time.time() - start) * 1000, 2),
                )

        ctx.record_result(delegation.delegation_id, result)
        return result

    # ----------------------------------------------------------
    # SINTESIS NARASI
    # ----------------------------------------------------------

    def _synthesize(
        self,
        user_input: str,
        results: List[AgentResult],
        total_latency_ms: float,
    ) -> str:
        """
        Ubah hasil terstruktur sub-agen menjadi narasi taktis Bahasa Indonesia.

        Strategi:
        1. Jika Vertex AI tersedia → gunakan GenerativeModel untuk narasi natural.
        2. Jika Vertex AI tidak tersedia → gunakan fallback narasi deterministik.

        Sesuai CLAUDE.md Seksi 8.1: narasi adalah tanggung jawab EKSKLUSIF Bang Jek.
        """
        if not results:
            return (
                "Hmm, Bang Jek tidak ngerti maksudnya nih. "
                "Coba sebutin lebih jelas ya, misalnya: "
                "'catat pendapatan 250 ribu', 'cek cuaca Sudirman', "
                "atau 'ingetin ganti oli besok jam 9'."
            )

        # Siapkan ringkasan hasil untuk prompt
        results_summary = self._format_results_for_synthesis(results)

        # Gunakan Vertex AI jika tersedia
        if self._model is not None:
            try:
                prompt = SYNTHESIS_PROMPT_TEMPLATE.format(
                    user_input=user_input,
                    agent_results_summary=results_summary,
                )
                response = self._model.generate_content(prompt)
                narration = response.text.strip()
                logger.log_agent_event(
                    "NARRATION_GENERATED: via Vertex AI",
                    agent_name=AGENT_NAME,
                    latency_ms=total_latency_ms,
                )
                return narration
            except Exception as e:
                logger.warning(
                    f"[Bang Jek] Vertex AI synthesis gagal: {e}. Menggunakan fallback."
                )

        # Fallback deterministik
        narration = _build_fallback_narration(user_input, results)
        logger.log_agent_event(
            "NARRATION_GENERATED: via fallback (deterministic)",
            agent_name=AGENT_NAME,
        )
        return narration

    def _format_results_for_synthesis(self, results: List[AgentResult]) -> str:
        """Format hasil sub-agen menjadi ringkasan terstruktur untuk synthesis prompt."""
        lines: List[str] = []
        for result in results:
            status_icon = "✅" if result.status == TaskStatus.COMPLETED else "❌"
            data_str = json.dumps(result.data, ensure_ascii=False, indent=2, default=str)
            lines.append(
                f"{status_icon} [{result.agent_name}] "
                f"Status: {result.status.value}\n"
                f"Data: {data_str}\n"
                f"Latency: {result.execution_time_ms or 0:.0f}ms"
            )
        return "\n\n".join(lines)
