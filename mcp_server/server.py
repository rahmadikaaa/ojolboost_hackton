"""
mcp_server/server.py
=====================
Layer 5 — MCP Server: Stateless JSON-RPC 2.0 Gateway.

TANGGUNG JAWAB:
Menjadi jembatan antara sub-agen (The Planner, The Archivist) dan
layanan eksternal (Google Calendar API, Google Keep/Notes API).

PRINSIP DESAIN (CLAUDE.md Seksi 9.1):
1. STATELESS MUTLAK   — Tidak ada state disimpan di dalam server ini.
                        State sementara → shared/context.py (session-scoped).
2. SCHEMA INTEGRITY   — Semua tipe data menggunakan import dari shared/schemas.py.
                        Tidak ada definisi type/model lokal di sini.
3. JSON-RPC 2.0       — Semua endpoint mengikuti spesifikasi JSON-RPC 2.0.
4. GUARDRAILS AWARE   — Setiap request divalidasi skemanya sebelum dieksekusi.
5. SINGLE ENDPOINT    — POST /mcp/call untuk semua tool invocation.
                        GET  /health    untuk health check Cloud Run.

TOOL REGISTRY:
  [The Planner]
  - create_calendar_event
  - create_task_reminder
  - list_upcoming_events

  [The Archivist]
  - save_note
  - search_notes
  - list_notes
  - update_note
"""

from __future__ import annotations

import json
import os
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ⚡ Matikan log JSON ke terminal — harus sebelum semua import agen
from shared.logger import suppress_console_logs
suppress_console_logs(suppress=True, log_file="ojolboost.log")

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS
from pydantic import ValidationError

# ============================================================
# IMPORT SCHEMAS dari shared/schemas.py — Single Source of Truth
# ATURAN (CLAUDE.md Seksi 3.4): Tidak ada definisi schema lokal.
# ============================================================
from shared.schemas import (
    NoteSchema,
    PlannerResultSchema,
    ScheduleEntrySchema,
    TaskStatus,
    ValidationResultSchema,
)
from shared.context import session_scope
from shared.logger import get_logger

logger = get_logger("mcp_server")

# ============================================================
# FLASK APP — Stateless WSGI
# Static folder: ../ui/ (folder UI web chat)
# ============================================================

_UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui")

app = Flask(__name__, static_folder=_UI_DIR, static_url_path="")
app.config["JSON_SORT_KEYS"] = False
CORS(app)  # Izinkan cross-origin request dari browser

# ============================================================
# ORCHESTRATOR SINGLETON — diinisialisasi sekali saat startup
# Semua /chat request memakai instance yang sama.
# ============================================================

_orchestrator = None


def _get_orchestrator():
    """
    Lazy-init singleton BangJekOrchestrator.
    Sub-agen di-register dengan graceful fallback jika belum tersedia.
    """
    global _orchestrator
    if _orchestrator is not None:
        return _orchestrator

    from agents.bang_jek.agent import BangJekOrchestrator

    orch = BangJekOrchestrator()
    orch.initialize()

    # Register sub-agen — graceful fallback jika belum ada implementasi
    _sub_agents_to_register = [
        ("The Auditor",        "agents.the_auditor.agent",       "TheAuditorAgent"),
        ("Demand Analytics",   "agents.demand_analytics.agent",  "DemandAnalyticsAgent"),
        ("Environmental",      "agents.environmental.agent",     "EnvironmentalAgent"),
        ("The Planner",        "agents.the_planner.agent",       "ThePlannerAgent"),
        ("The Archivist",      "agents.the_archivist.agent",     "TheArchivistAgent"),
    ]

    for agent_name, module_path, class_name in _sub_agents_to_register:
        try:
            import importlib
            module = importlib.import_module(module_path)
            agent_class = getattr(module, class_name)
            orch.register_sub_agent(agent_name, agent_class())
            logger.info(f"[Server] Sub-agent registered: {agent_name}")
        except Exception as e:
            logger.warning(f"[Server] Sub-agent '{agent_name}' tidak tersedia: {e}")

    _orchestrator = orch
    return _orchestrator


# ============================================================
# ENVIRONMENT CONFIG
# ============================================================

GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")
CALENDAR_TIMEZONE: str = "Asia/Jakarta"
MCP_SERVER_PORT: int = int(os.getenv("MCP_SERVER_PORT", "8080"))
MCP_REQUEST_TIMEOUT: int = int(os.getenv("MCP_REQUEST_TIMEOUT", "10"))

# ============================================================
# JSON-RPC 2.0 RESPONSE BUILDERS
# Referensi spesifikasi: https://www.jsonrpc.org/specification
# ============================================================

# Kode error standar JSON-RPC 2.0
JSONRPC_PARSE_ERROR      = -32700
JSONRPC_INVALID_REQUEST  = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS   = -32602
JSONRPC_INTERNAL_ERROR   = -32603

# Kode error custom OjolBoost (di luar range JSON-RPC standar)
OJOL_SCHEMA_ERROR        = -32001
OJOL_EXTERNAL_API_ERROR  = -32002
OJOL_TIMEOUT_ERROR       = -32003


def _rpc_success(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    """Bangun response JSON-RPC 2.0 sukses."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def _rpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: Optional[Any] = None,
) -> Dict[str, Any]:
    """Bangun response JSON-RPC 2.0 error."""
    error_obj: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error_obj["data"] = data
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error_obj,
    }


def _validate_jsonrpc_envelope(body: Dict[str, Any]) -> Optional[tuple]:
    """
    Validasi struktur dasar JSON-RPC 2.0 request envelope.

    Returns:
        None jika valid.
        Tuple (error_response, http_status) jika tidak valid.
    """
    req_id = body.get("id")

    if body.get("jsonrpc") != "2.0":
        return (
            _rpc_error(req_id, JSONRPC_INVALID_REQUEST,
                       "Field 'jsonrpc' harus bernilai '2.0'."),
            400,
        )

    if "method" not in body:
        return (
            _rpc_error(req_id, JSONRPC_INVALID_REQUEST,
                       "Field 'method' wajib ada."),
            400,
        )

    if body.get("method") != "tools/call":
        return (
            _rpc_error(req_id, JSONRPC_METHOD_NOT_FOUND,
                       f"Method '{body.get('method')}' tidak dikenal. "
                       f"Method yang tersedia: 'tools/call'."),
            404,
        )

    params = body.get("params", {})
    if "name" not in params:
        return (
            _rpc_error(req_id, JSONRPC_INVALID_PARAMS,
                       "Field 'params.name' (nama tool) wajib ada."),
            400,
        )

    return None


# ============================================================
# TOOL REGISTRY — Dispatcher map
# Setiap tool terdaftar dengan:
#   "tool_name": (handler_function, required_fields_set)
# ============================================================

def _dispatch_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    request_id: Any,
) -> tuple[Dict[str, Any], int]:
    """
    Dispatch tool call ke handler yang sesuai.

    Returns:
        Tuple (json_rpc_response_dict, http_status_code)
    """
    TOOL_REGISTRY = {
        # -- The Planner tools --
        "create_calendar_event": _handle_create_calendar_event,
        "create_task_reminder":  _handle_create_task_reminder,
        "list_upcoming_events":  _handle_list_upcoming_events,
        # -- The Archivist tools --
        "save_note":    _handle_save_note,
        "search_notes": _handle_search_notes,
        "list_notes":   _handle_list_notes,
        "update_note":  _handle_update_note,
    }

    handler = TOOL_REGISTRY.get(tool_name)
    if handler is None:
        available = ", ".join(TOOL_REGISTRY.keys())
        return (
            _rpc_error(
                request_id,
                JSONRPC_METHOD_NOT_FOUND,
                f"Tool '{tool_name}' tidak terdaftar. "
                f"Tool yang tersedia: {available}.",
            ),
            404,
        )

    try:
        result = handler(arguments)
        return _rpc_success(request_id, result), 200

    except ValidationError as e:
        # Payload tidak sesuai Pydantic schema dari shared/schemas.py
        errors = [
            {"field": ".".join(str(l) for l in err["loc"]), "message": err["msg"]}
            for err in e.errors()
        ]
        return (
            _rpc_error(
                request_id,
                OJOL_SCHEMA_ERROR,
                f"Payload tidak sesuai schema shared/schemas.py untuk tool '{tool_name}'.",
                data={"validation_errors": errors},
            ),
            422,
        )

    except TimeoutError as e:
        return (
            _rpc_error(
                request_id,
                OJOL_TIMEOUT_ERROR,
                f"Tool '{tool_name}' timeout: {str(e)}",
            ),
            504,
        )

    except ExternalAPIError as e:
        return (
            _rpc_error(
                request_id,
                OJOL_EXTERNAL_API_ERROR,
                f"Error dari API eksternal pada tool '{tool_name}': {str(e)}",
                data={"api_name": e.api_name, "status_code": e.status_code},
            ),
            502,
        )

    except Exception as e:
        logger.error(
            f"[MCP Server] Internal error pada tool '{tool_name}': {e}\n"
            f"{traceback.format_exc()}"
        )
        return (
            _rpc_error(
                request_id,
                JSONRPC_INTERNAL_ERROR,
                f"Internal server error pada tool '{tool_name}'.",
                data={"detail": str(e)} if os.getenv("DEBUG") == "true" else None,
            ),
            500,
        )


# ============================================================
# CUSTOM EXCEPTIONS
# ============================================================

class ExternalAPIError(Exception):
    """Raised ketika Google Calendar atau Google Keep API mengembalikan error."""

    def __init__(self, message: str, api_name: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.api_name = api_name
        self.status_code = status_code


# ============================================================
# GOOGLE CALENDAR & KEEP API CLIENTS
# Lazy-initialized agar tidak gagal saat startup tanpa credentials.
# ============================================================

_calendar_service = None
_keep_client = None


def _get_calendar_service():
    """Lazy-init Google Calendar API client."""
    global _calendar_service
    if _calendar_service is None:
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if creds_path:
                creds = service_account.Credentials.from_service_account_file(
                    creds_path,
                    scopes=["https://www.googleapis.com/auth/calendar"],
                )
                _calendar_service = build("calendar", "v3", credentials=creds)
            else:
                import google.auth
                creds, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/calendar"]
                )
                _calendar_service = build("calendar", "v3", credentials=creds)

            logger.info("[MCP Server] Google Calendar client initialized.")
        except Exception as e:
            logger.warning(f"[MCP Server] Google Calendar tidak tersedia: {e}")
            raise ExternalAPIError(
                f"Google Calendar API tidak dapat diinisialisasi: {e}",
                api_name="Google Calendar",
            ) from e
    return _calendar_service


def _get_keep_client():
    """Lazy-init Google Keep API client (gkeepapi atau alternative)."""
    global _keep_client
    if _keep_client is None:
        try:
            import gkeepapi

            token = os.getenv("GOOGLE_KEEP_TOKEN")
            email = os.getenv("GOOGLE_ACCOUNT_EMAIL")

            if not token or not email:
                raise EnvironmentError(
                    "GOOGLE_KEEP_TOKEN dan GOOGLE_ACCOUNT_EMAIL wajib dikonfigurasi."
                )

            keep = gkeepapi.Keep()
            keep.resume(email, token)
            _keep_client = keep
            logger.info("[MCP Server] Google Keep client initialized.")
        except Exception as e:
            logger.warning(f"[MCP Server] Google Keep tidak tersedia: {e}")
            raise ExternalAPIError(
                f"Google Keep API tidak dapat diinisialisasi: {e}",
                api_name="Google Keep",
            ) from e
    return _keep_client


# ============================================================
# TOOL HANDLERS — The Planner
# Schema input divalidasi via shared/schemas.py
# ============================================================

def _handle_create_calendar_event(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handler untuk create_calendar_event.
    Schema: ScheduleEntrySchema dari shared/schemas.py.
    """
    # Validasi schema dari shared/schemas.py — single source of truth
    entry = ScheduleEntrySchema(
        title=args.get("title", ""),
        scheduled_at=args.get("start_datetime", args.get("scheduled_at")),
        duration_minutes=args.get("duration_minutes", 30),
        description=args.get("description"),
        reminder_minutes_before=args.get("reminder_minutes", 15),
        location=args.get("location"),
    )

    # Hitung end time dari ScheduleEntrySchema (sudah tervalidasi Pydantic)
    from datetime import timedelta
    end_dt = entry.scheduled_at + timedelta(minutes=entry.duration_minutes)

    # Bangun event body untuk Google Calendar API
    event_body = {
        "summary": entry.title,
        "description": entry.description or "",
        "location": entry.location or "",
        "start": {
            "dateTime": entry.scheduled_at.isoformat(),
            "timeZone": args.get("timezone", CALENDAR_TIMEZONE),
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": args.get("timezone", CALENDAR_TIMEZONE),
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": entry.reminder_minutes_before},
            ],
        },
    }

    try:
        service = _get_calendar_service()
        created = service.events().insert(
            calendarId=args.get("calendar_id", GOOGLE_CALENDAR_ID),
            body=event_body,
        ).execute()

        logger.log_agent_event(
            f"CAL_EVENT_CREATED: '{entry.title}' at {entry.scheduled_at.isoformat()}",
            agent_name="MCP Server",
            event_id=created.get("id"),
        )

        return {
            "id": created.get("id"),
            "status": "confirmed",
            "htmlLink": created.get("htmlLink"),
            "summary": created.get("summary"),
            "start": created.get("start"),
            "end": created.get("end"),
        }

    except ExternalAPIError:
        raise
    except Exception as e:
        raise ExternalAPIError(
            f"Gagal membuat calendar event: {e}",
            api_name="Google Calendar",
        ) from e


def _handle_create_task_reminder(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handler untuk create_task_reminder.
    Menggunakan Google Calendar dengan event pendek sebagai task reminder.
    """
    title = args.get("title", "")
    if not title or len(title.strip()) < 3:
        raise ValidationError(
            [{"loc": ("title",), "msg": "Judul reminder minimal 3 karakter.", "type": "value_error"}],
            model=ScheduleEntrySchema,
        )

    due_dt_str = args.get("due_datetime")
    if not due_dt_str:
        raise ValueError("due_datetime wajib ada untuk create_task_reminder.")

    due_dt = datetime.fromisoformat(due_dt_str)
    duration = args.get("duration_minutes", 30)
    reminder_minutes = args.get("reminder_minutes_before", 15)
    notes = args.get("notes", "")
    recurrence = args.get("recurrence")

    entry = ScheduleEntrySchema(
        title=title,
        scheduled_at=due_dt,
        duration_minutes=duration,
        description=notes,
        reminder_minutes_before=reminder_minutes,
    )

    from datetime import timedelta
    end_dt = entry.scheduled_at + timedelta(minutes=entry.duration_minutes)

    recurrence_rule = []
    if recurrence == "daily":
        recurrence_rule = ["RRULE:FREQ=DAILY"]
    elif recurrence == "weekly":
        recurrence_rule = ["RRULE:FREQ=WEEKLY"]
    elif recurrence == "monthly":
        recurrence_rule = ["RRULE:FREQ=MONTHLY"]

    event_body = {
        "summary": f"[REMINDER] {entry.title}",
        "description": notes,
        "start": {
            "dateTime": entry.scheduled_at.isoformat(),
            "timeZone": args.get("timezone", CALENDAR_TIMEZONE),
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": args.get("timezone", CALENDAR_TIMEZONE),
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": entry.reminder_minutes_before},
            ],
        },
    }

    if recurrence_rule:
        event_body["recurrence"] = recurrence_rule

    try:
        service = _get_calendar_service()
        created = service.events().insert(
            calendarId=GOOGLE_CALENDAR_ID,
            body=event_body,
        ).execute()

        logger.log_agent_event(
            f"TASK_REMINDER_CREATED: '{entry.title}' due={entry.scheduled_at.isoformat()}",
            agent_name="MCP Server",
            event_id=created.get("id"),
        )

        return {
            "id": created.get("id"),
            "title": entry.title,
            "status": TaskStatus.COMPLETED.value,
            "scheduled_at": entry.scheduled_at.isoformat(),
            "reminder_set": True,
        }

    except ExternalAPIError:
        raise
    except Exception as e:
        raise ExternalAPIError(
            f"Gagal membuat task reminder: {e}",
            api_name="Google Calendar",
        ) from e


def _handle_list_upcoming_events(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handler untuk list_upcoming_events.
    Kembalikan event dalam rentang waktu yang diminta.
    """
    time_min = args.get("time_min", datetime.now(tz=timezone.utc).isoformat())
    time_max = args.get(
        "time_max",
        (datetime.now(tz=timezone.utc).replace(hour=23, minute=59)).isoformat(),
    )
    max_results = min(int(args.get("max_results", 10)), 50)   # Batasi maks 50
    calendar_id = args.get("calendar_id", GOOGLE_CALENDAR_ID)

    try:
        service = _get_calendar_service()
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = events_result.get("items", [])

        return {
            "events": [
                {
                    "id": e.get("id"),
                    "summary": e.get("summary", ""),
                    "start": e.get("start", {}),
                    "end": e.get("end", {}),
                    "description": e.get("description", ""),
                }
                for e in events
            ],
            "total": len(events),
            "time_min": time_min,
            "time_max": time_max,
        }

    except ExternalAPIError:
        raise
    except Exception as e:
        raise ExternalAPIError(
            f"Gagal mengambil daftar event: {e}",
            api_name="Google Calendar",
        ) from e


# ============================================================
# TOOL HANDLERS — The Archivist
# Schema input divalidasi via shared/schemas.py (NoteSchema)
# ============================================================

def _handle_save_note(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handler untuk save_note.
    Schema: NoteSchema dari shared/schemas.py — single source of truth.
    """
    # Validasi schema dari shared/schemas.py
    note = NoteSchema(
        title=args.get("title", ""),
        content=args.get("content", ""),
        tags=args.get("labels", args.get("tags", [])),
    )

    try:
        keep = _get_keep_client()
        gnote = keep.createNote(note.title, note.content)

        # Tambahkan labels/tags
        for tag in note.tags:
            try:
                label = keep.findLabel(tag.lstrip("#"))
                if label is None:
                    label = keep.createLabel(tag.lstrip("#"))
                gnote.labels.add(label)
            except Exception:
                pass  # Label creation tidak boleh memblokir save

        keep.sync()

        logger.log_agent_event(
            f"NOTE_SAVED: '{note.title}', tags={note.tags}",
            agent_name="MCP Server",
            note_id=gnote.id,
        )

        return {
            "id": gnote.id,
            "title": note.title,
            "content": note.content,
            "labels": note.tags,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "url": f"https://keep.google.com/u/0/#NOTE/{gnote.id}",
        }

    except ExternalAPIError:
        raise
    except Exception as e:
        raise ExternalAPIError(
            f"Gagal menyimpan catatan ke Google Keep: {e}",
            api_name="Google Keep",
        ) from e


def _handle_search_notes(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handler untuk search_notes.
    Mendukung pencarian via query text dan/atau labels.
    Referensi: skills/the_archivist/semantic_search.md.
    """
    query = args.get("query", "")
    labels = args.get("labels", [])
    max_results = min(int(args.get("max_results", 10)), 10)

    try:
        keep = _get_keep_client()
        keep.sync()

        all_notes = list(keep.all())
        results = []

        for note in all_notes:
            if len(results) >= max_results:
                break

            # Filter by label (tag-based search)
            if labels:
                note_label_names = [
                    l.name.lower()
                    for l in note.labels.all()
                ]
                if not any(
                    lbl.lstrip("#").lower() in note_label_names
                    for lbl in labels
                ):
                    continue

            # Filter by query text (keyword search)
            searchable = (
                (note.title or "") + " " + (note.text or "")
            ).lower()
            if query and query.lower() not in searchable:
                continue

            results.append({
                "id": note.id,
                "title": note.title or "",
                "text": (note.text or "")[:500],
                "labels": [
                    f"#{l.name}" for l in note.labels.all()
                ],
                "created_at": note.timestamps.created.isoformat()
                              if note.timestamps else None,
                "url": f"https://keep.google.com/u/0/#NOTE/{note.id}",
            })

        logger.log_agent_event(
            f"NOTES_SEARCHED: query='{query[:40]}', found={len(results)}",
            agent_name="MCP Server",
        )

        return {
            "notes": results,
            "total": len(results),
            "query": query,
        }

    except ExternalAPIError:
        raise
    except Exception as e:
        raise ExternalAPIError(
            f"Gagal mencari catatan di Google Keep: {e}",
            api_name="Google Keep",
        ) from e


def _handle_list_notes(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handler untuk list_notes.
    Kembalikan N catatan terbaru dari Google Keep.
    """
    max_results = min(int(args.get("max_results", 20)), 50)
    days_back = int(args.get("days_back", 7))

    try:
        keep = _get_keep_client()
        keep.sync()

        from datetime import timedelta
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days_back)

        all_notes = list(keep.all())
        results = []

        for note in all_notes:
            if len(results) >= max_results:
                break
            try:
                created = note.timestamps.created.replace(tzinfo=timezone.utc)
                if created >= cutoff:
                    results.append({
                        "id": note.id,
                        "title": note.title or "",
                        "labels": [f"#{l.name}" for l in note.labels.all()],
                        "created_at": created.isoformat(),
                    })
            except Exception:
                continue

        # Sort terbaru dulu
        results.sort(key=lambda n: n.get("created_at", ""), reverse=True)

        return {"notes": results, "total": len(results)}

    except ExternalAPIError:
        raise
    except Exception as e:
        raise ExternalAPIError(
            f"Gagal mengambil daftar catatan dari Google Keep: {e}",
            api_name="Google Keep",
        ) from e


def _handle_update_note(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handler untuk update_note (append content).
    Digunakan ketika The Archivist mendeteksi duplikasi dan ingin append.
    """
    note_id = args.get("note_id", "")
    append_content = args.get("append_content", "")

    if not note_id:
        raise ValueError("note_id wajib ada untuk update_note.")
    if not append_content:
        raise ValueError("append_content tidak boleh kosong.")

    try:
        keep = _get_keep_client()
        keep.sync()

        note = keep.get(note_id)
        if note is None:
            return {
                "success": False,
                "error": f"Catatan dengan id '{note_id}' tidak ditemukan.",
            }

        note.text = (note.text or "") + append_content
        keep.sync()

        logger.log_agent_event(
            f"NOTE_UPDATED: note_id={note_id}, appended {len(append_content)} chars",
            agent_name="MCP Server",
        )

        return {
            "success": True,
            "note_id": note_id,
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    except ExternalAPIError:
        raise
    except Exception as e:
        raise ExternalAPIError(
            f"Gagal memperbarui catatan di Google Keep: {e}",
            api_name="Google Keep",
        ) from e


# ============================================================
# FLASK ROUTES
# ============================================================
# ============================================================
# FLASK ROUTES
# ============================================================

@app.route("/", methods=["GET"])
def root_index() -> Response:
    """Serve Web Chat UI (ui/index.html)."""
    return send_from_directory(_UI_DIR, "index.html")

@app.route("/health", methods=["GET"])
def health_check() -> Response:
    """
    Health check endpoint untuk Google Cloud Run.
    Mengembalikan 200 OK dan versi server.
    """
    return jsonify({
        "status": "healthy",
        "service": "OjolBoost MCP Server",
        "version": "2.0.0",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "tools_available": [
            "create_calendar_event",
            "create_task_reminder",
            "list_upcoming_events",
            "save_note",
            "search_notes",
            "list_notes",
            "update_note",
        ],
    }), 200


@app.route("/mcp/call", methods=["POST"])
def mcp_call() -> Response:
    """
    Single JSON-RPC 2.0 endpoint untuk semua tool invocation.

    Request format:
        {
            "jsonrpc": "2.0",
            "id": "<request_id>",
            "method": "tools/call",
            "params": {
                "name": "<tool_name>",
                "arguments": { ... }
            }
        }

    Response format (sukses):
        {
            "jsonrpc": "2.0",
            "id": "<request_id>",
            "result": { ... }
        }

    Response format (error):
        {
            "jsonrpc": "2.0",
            "id": "<request_id>",
            "error": {
                "code": <int>,
                "message": "<string>",
                "data": { ... }   // opsional
            }
        }
    """
    req_id = None   # Inisialisasi sebelum parsing untuk error handling

    # --- Parse JSON body ---
    try:
        body = request.get_json(force=True, silent=False)
        if body is None:
            raise ValueError("Body kosong atau bukan JSON valid.")
    except Exception:
        return jsonify(
            _rpc_error(None, JSONRPC_PARSE_ERROR,
                       "Request body bukan JSON yang valid.")
        ), 400

    req_id = body.get("id")

    # --- Validasi envelope JSON-RPC 2.0 ---
    envelope_error = _validate_jsonrpc_envelope(body)
    if envelope_error:
        error_response, http_status = envelope_error
        return jsonify(error_response), http_status

    # --- Ekstrak tool info ---
    params = body.get("params", {})
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    # --- Log request ---
    caller_agent = request.headers.get("X-Agent-Name", "unknown")
    logger.info(
        f"[MCP Server] REQUEST: tool='{tool_name}' from agent='{caller_agent}' "
        f"id='{req_id}'"
    )

    # --- STATELESS: Gunakan session scope dari shared/context.py ---
    with session_scope(f"mcp:{tool_name}:{req_id}") as ctx:
        ctx.set_metadata("tool_name", tool_name)
        ctx.set_metadata("caller_agent", caller_agent)
        ctx.set_metadata("request_id", str(req_id))

        # --- Dispatch ke tool handler ---
        rpc_response, http_status = _dispatch_tool(tool_name, arguments, req_id)

        logger.info(
            f"[MCP Server] RESPONSE: tool='{tool_name}' "
            f"success={'error' not in rpc_response} "
            f"http={http_status}"
        )

    return jsonify(rpc_response), http_status


# ============================================================
# WEB CHAT ENDPOINT — Untuk UI web chat
# POST /chat  { "message": "...", "driver_id": "..." }
# ============================================================

@app.route("/chat", methods=["POST"])
def chat() -> Response:
    """
    Endpoint utama untuk Web Chat UI.
    Menerima pesan pengguna dan mengembalikan respons Bang Jek.

    Request Body:
        { "message": "cek cuaca Sudirman", "driver_id": "DRIVER_001" }

    Response:
        {
            "narration": "Bang, Sudirman lagi mendung...",
            "agents_called": ["Environmental"],
            "latency_ms": 1234.5,
            "status": "ok"
        }
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
        message = (body.get("message") or "").strip()
        driver_id = body.get("driver_id", "DRIVER_WEB_001")

        if not message:
            return jsonify({
                "narration": "Hmm, pesannya kosong Bang. Coba tulis lagi ya!",
                "agents_called": [],
                "latency_ms": 0,
                "status": "error",
            }), 400

        orch = _get_orchestrator()
        response = orch.process(message, driver_id=driver_id)

        logger.info(
            f"[/chat] message='{message[:60]}...', "
            f"agents={response.agents_called}, "
            f"latency={response.total_latency_ms:.0f}ms"
        )

        return jsonify({
            "narration": response.narration,
            "agents_called": response.agents_called,
            "latency_ms": response.total_latency_ms,
            "status": "ok",
        }), 200

    except Exception as e:
        logger.error(f"[/chat] Internal error: {e}\n{traceback.format_exc()}")
        return jsonify({
            "narration": "Aduh Bang, Bang Jek lagi ada masalah internal nih. Coba lagi sebentar ya!",
            "agents_called": [],
            "latency_ms": 0,
            "status": "error",
            "detail": str(e) if os.getenv("DEBUG") == "true" else None,
        }), 500


@app.errorhandler(404)
def not_found(e) -> Response:
    return jsonify(
        _rpc_error(None, JSONRPC_METHOD_NOT_FOUND,
                   f"Endpoint tidak ditemukan: {request.path}. "
                   f"Gunakan POST /mcp/call atau GET /health.")
    ), 404


@app.errorhandler(405)
def method_not_allowed(e) -> Response:
    return jsonify(
        _rpc_error(None, JSONRPC_INVALID_REQUEST,
                   f"HTTP method '{request.method}' tidak diizinkan pada endpoint ini.")
    ), 405


@app.errorhandler(500)
def internal_error(e) -> Response:
    logger.error(f"[MCP Server] Unhandled 500: {e}")
    return jsonify(
        _rpc_error(None, JSONRPC_INTERNAL_ERROR,
                   "Internal server error yang tidak ditangani.")
    ), 500


# ============================================================
# ENTRYPOINT — Development server
# Production: gunakan Gunicorn di Dockerfile (lihat deploy/)
# ============================================================

if __name__ == "__main__":
    logger.info(
        f"[MCP Server] Starting OjolBoost MCP Server v2.0.0 "
        f"on port {MCP_SERVER_PORT}"
    )
    app.run(
        host="0.0.0.0",
        port=MCP_SERVER_PORT,
        debug=os.getenv("DEBUG", "false").lower() == "true",
    )
