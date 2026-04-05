"""
shared/schemas.py
=================
Layer 1 — Single Source of Truth untuk semua tipe data.

ATURAN (CLAUDE.md Seksi 3.4):
- Semua Pydantic model yang digunakan lintas agen WAJIB didefinisikan di sini.
- Tidak ada agent yang boleh mendefinisikan schema data sendiri di luar file ini.
- Semua import schema dari modul lain HARUS mengacu ke file ini.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ============================================================
# ENUMS — Domain Value Objects
# ============================================================

class AlertLevel(str, Enum):
    """Tingkat keparahan alert. Digunakan oleh Environmental agent."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskStatus(str, Enum):
    """Status eksekusi tugas. Digunakan di seluruh sistem untuk state tracking."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class WeatherCondition(str, Enum):
    """Kondisi cuaca yang dikenali sistem. Diparsing dari OpenWeather response."""
    CLEAR = "clear"
    CLOUDY = "cloudy"
    RAIN = "rain"
    HEAVY_RAIN = "heavy_rain"
    STORM = "storm"
    UNKNOWN = "unknown"


class ServiceType(str, Enum):
    """Jenis layanan ojek online. Digunakan untuk rekomendasi pivot strategi."""
    RIDE = "ride"
    FOOD = "food"
    PACKAGE = "package"


class SqlOperation(str, Enum):
    """Operasi SQL yang diidentifikasi validator. Digunakan oleh AuditorValidator."""
    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    DROP = "DROP"
    TRUNCATE = "TRUNCATE"
    CREATE = "CREATE"
    ALTER = "ALTER"
    UNKNOWN = "UNKNOWN"


# ============================================================
# LAYER 1 — AGENT ORCHESTRATION SCHEMAS
# Digunakan oleh Bang Jek untuk delegasi dan sintesis hasil.
# ============================================================

class AgentDelegation(BaseModel):
    """
    Payload yang dikirim Bang Jek saat mendelegasikan tugas ke sub-agen.
    Sesuai pola 'delegate only' pada CLAUDE.md Seksi 1.3.
    """
    delegation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_name: str = Field(description="Nama agen tujuan, PERSIS sesuai CLAUDE.md Seksi 4.3")
    task: str = Field(description="Deskripsi tugas dalam Bahasa Indonesia")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Konteks tambahan jika diperlukan")
    priority: int = Field(default=5, ge=1, le=10, description="Prioritas 1 (rendah) - 10 (kritis)")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("agent_name")
    @classmethod
    def validate_agent_name(cls, v: str) -> str:
        """Pastikan nama agen sesuai konvensi CLAUDE.md Seksi 4.3."""
        allowed = {"Bang Jek", "Demand Analytics", "Environmental", "The Planner", "The Archivist", "The Auditor"}
        if v not in allowed:
            raise ValueError(f"Nama agen '{v}' tidak valid. Harus salah satu dari: {allowed}")
        return v


class AgentResult(BaseModel):
    """
    Hasil yang dikembalikan sub-agen ke Bang Jek.
    Sesuai pola 'results only' pada CLAUDE.md Seksi 1.3.
    """
    delegation_id: Optional[str] = None
    agent_name: str
    status: TaskStatus
    data: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    execution_time_ms: Optional[float] = None
    completed_at: datetime = Field(default_factory=datetime.utcnow)


class BangJekResponseSchema(BaseModel):
    """Schema output akhir yang dikirim Bang Jek ke pengguna."""
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_input: str
    agent_results: List[AgentResult]
    narration: str = Field(description="Narasi taktis Bahasa Indonesia untuk pengguna")
    total_latency_ms: float
    agents_called: List[str]
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# THE AUDITOR — Financial Transaction Schemas
# Digunakan juga di auditor_validator.py dan transaction_schema.md
# ============================================================

class TransactionSchema(BaseModel):
    """
    Schema wajib untuk pencatatan transaksi ke BigQuery.

    BigQuery Table: `ojolboosttrack2.trx_daily_income`
    Didefinisikan lengkap di: skills/the_auditor/transaction_schema.md
    """
    amount: float = Field(gt=0, description="Pendapatan dalam rupiah (harus > 0)")
    transaction_date: datetime = Field(description="Tanggal & waktu transaksi (UTC)")
    service_type: ServiceType = Field(description="Jenis layanan: ride/food/package")
    zone: Optional[str] = Field(default=None, max_length=100, description="Nama zona/area pickup")
    notes: Optional[str] = Field(default=None, max_length=500, description="Catatan tambahan")
    driver_id: Optional[str] = Field(default=None, max_length=50, description="ID pengemudi")

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        """Validasi tambahan: jumlah tidak boleh melebihi 10 juta per transaksi (anomali guard)."""
        if v > 10_000_000:
            raise ValueError("Jumlah transaksi melebihi batas wajar (10 juta). Periksa input.")
        return v


class TransactionRecord(TransactionSchema):
    """Schema lengkap transaksi setelah tersimpan di BigQuery (includes generated fields)."""
    transaction_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    status: str = Field(default="recorded")


class AuditorResultSchema(BaseModel):
    """Output The Auditor yang dikembalikan ke Bang Jek."""
    transaction_id: Optional[str] = None
    operation: str
    table: str
    status: TaskStatus
    balance_snapshot: Optional[float] = Field(default=None, description="Total pendapatan hari ini (Rp)")
    records_affected: Optional[int] = None
    error_message: Optional[str] = None


class DailyFinancialReportSchema(BaseModel):
    """Schema laporan keuangan harian dari BigQuery."""
    report_date: str = Field(description="Format: YYYY-MM-DD")
    total_income: float
    transaction_count: int
    by_service_type: Dict[str, float] = Field(default_factory=dict)
    top_zone: Optional[str] = None
    average_per_trip: Optional[float] = None


# ============================================================
# ENVIRONMENTAL — Weather Schemas
# ============================================================

class WeatherResponseSchema(BaseModel):
    """Output dari OpenWeather API setelah diparsing oleh Environmental agent."""
    location: str
    condition: WeatherCondition
    temperature_celsius: float
    humidity_percent: float
    alert_level: AlertLevel
    pivot_recommendation: Optional[str] = Field(
        default=None,
        description="Rekomendasi pivot layanan berdasarkan cuaca"
    )
    raw_data: Optional[Dict[str, Any]] = Field(default=None, description="Raw OpenWeather response")
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# DEMAND ANALYTICS — Zone & Demand Schemas
# ============================================================

class ZoneDemandSchema(BaseModel):
    """Permintaan zona tunggal dari analisis BigQuery."""
    zone_name: str
    probability_score: float = Field(ge=0.0, le=1.0, description="Skor 0.0 - 1.0")
    demand_trend: str = Field(description="'rising', 'falling', atau 'stable'")
    recommended_service: ServiceType
    historical_avg: Optional[float] = Field(default=None, description="Rata-rata trip historis per jam")
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("demand_trend")
    @classmethod
    def validate_trend(cls, v: str) -> str:
        if v not in {"rising", "falling", "stable"}:
            raise ValueError("demand_trend harus 'rising', 'falling', atau 'stable'")
        return v


class DemandAnalyticsResultSchema(BaseModel):
    """Output Demand Analytics yang dikembalikan ke Bang Jek."""
    zones: List[ZoneDemandSchema]
    recommendation: str = Field(description="Rekomendasi taktis dalam format data")
    confidence: float = Field(ge=0.0, le=1.0)
    query_executed: Optional[str] = Field(default=None, description="SQL yang dieksekusi (untuk audit)")
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# THE PLANNER — Schedule & Task Schemas
# ============================================================

class ScheduleEntrySchema(BaseModel):
    """Entry kalender yang akan dibuat oleh The Planner."""
    title: str = Field(min_length=3, max_length=200)
    scheduled_at: datetime
    duration_minutes: int = Field(default=30, ge=5, le=480)
    description: Optional[str] = Field(default=None, max_length=1000)
    reminder_minutes_before: int = Field(default=15, ge=0, le=1440)
    location: Optional[str] = Field(default=None, max_length=200)


class PlannerResultSchema(BaseModel):
    """Output The Planner yang dikembalikan ke Bang Jek."""
    event_id: str
    status: TaskStatus
    scheduled_at: datetime
    title: str
    reminder_set: bool = False
    calendar_link: Optional[str] = None


# ============================================================
# THE ARCHIVIST — Note & Knowledge Schemas
# ============================================================

class NoteSchema(BaseModel):
    """Catatan yang akan disimpan oleh The Archivist ke Google Keep/Notes."""
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=5000)
    tags: List[str] = Field(default_factory=list, description="Tag untuk indexing")


class NoteResultSchema(NoteSchema):
    """Output The Archivist setelah menyimpan catatan."""
    note_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    url: Optional[str] = None


class SearchResultSchema(BaseModel):
    """Hasil pencarian catatan oleh The Archivist."""
    query: str
    results: List[NoteResultSchema]
    total_found: int


# ============================================================
# GUARDRAILS — Validation Schemas
# Digunakan oleh guardrails/auditor_validator.py
# ============================================================

class ToolCallSchema(BaseModel):
    """Metadata setiap pemanggilan tool, digunakan oleh PreToolUse hook."""
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str
    agent_name: str
    parameters: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ValidationResultSchema(BaseModel):
    """Hasil validasi deterministik dari AuditorValidator."""
    is_valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    operation_detected: Optional[SqlOperation] = None
    validated_at: datetime = Field(default_factory=datetime.utcnow)


class GuardrailLogSchema(BaseModel):
    """Log entry untuk setiap event hook yang dieksekusi."""
    event_type: str = Field(description="'pre_tool_use' atau 'post_tool_use'")
    agent_name: str
    tool_name: str
    validation_result: Optional[ValidationResultSchema] = None
    was_blocked: bool = False
    timestamp: datetime = Field(default_factory=datetime.utcnow)
