"""
agents/the_archivist/tools.py
================================
Layer 4 — MCP Client Tool Wrappers untuk The Archivist.

Referensi skill:
  - skills/the_archivist/note_indexing.md
  - skills/the_archivist/semantic_search.md
  - skills/the_archivist/keep_sync_protocol.md

Semua komunikasi melalui Model Context Protocol (MCP) ke MCP_SERVER_HOST.
Protokol: JSON-RPC 2.0 over HTTP POST ke endpoint /mcp/call.

ATURAN (CLAUDE.md Seksi 6.4):
- Hanya akses MCP Google Notes / Keep.
- Tidak ada operasi DELETE — hanya save, search, list.
- Cek duplikasi sebelum menyimpan catatan baru.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from agents.the_archivist.config import (
    AGENT_NAME,
    AUTO_TAG_RULES,
    DUPLICATE_CHECK_DAYS,
    DUPLICATE_SIMILARITY_THRESHOLD,
    MAX_SEARCH_RESULTS,
    MCP_BASE_URL,
    MCP_MAX_RETRIES,
    MCP_TIMEOUT_SECONDS,
    NOTE_TITLE_PREFIXES,
    SEARCH_CACHE_TTL_SECONDS,
    VALID_TAGS,
)
from shared.logger import get_logger
from shared.schemas import NoteResultSchema, NoteSchema, SearchResultSchema, TaskStatus

logger = get_logger("the_archivist.tools")


# ============================================================
# MCP CLIENT — The Archivist edition
# Sama strukturnya dengan The Planner MCPClient,
# namun dengan header X-Agent-Name berbeda dan TTL cache search.
# Referensi: skills/the_archivist/keep_sync_protocol.md
# ============================================================

class MCPClient:
    """
    Client untuk berkomunikasi dengan MCP Server via HTTP JSON-RPC 2.0.
    Digunakan oleh The Archivist untuk Google Keep/Notes operations.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Agent-Name": AGENT_NAME,
        })

    def call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        retries: int = MCP_MAX_RETRIES,
    ) -> Dict[str, Any]:
        """
        Panggil tool MCP via JSON-RPC 2.0.
        Retry logic sesuai skills/the_archivist/keep_sync_protocol.md.

        Raises:
            RuntimeError: Jika MCP unreachable setelah retry → fallback ke pending_notes.
            ValueError: Jika error logika non-retriable (400, 401).
        """
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

        last_error: Optional[Exception] = None

        for attempt in range(1, retries + 2):
            try:
                logger.info(
                    f"[The Archivist] MCP call: {tool_name} (attempt {attempt})"
                )
                response = self._session.post(
                    f"{MCP_BASE_URL}/mcp/call",
                    json=payload,
                    timeout=MCP_TIMEOUT_SECONDS,
                )

                # Kode yang di-retry (sesuai keep_sync_protocol.md)
                if response.status_code in (408, 503):
                    raise requests.ConnectionError(f"Retriable HTTP {response.status_code}")

                # Kode yang TIDAK di-retry
                if response.status_code == 401:
                    raise ValueError("TOKEN_EXPIRED: Koneksi ke Google Notes perlu diperbarui.")
                if response.status_code == 400:
                    raise ValueError(f"Bad request ke MCP: {response.text[:200]}")

                if response.status_code == 429:
                    # Rate limit — tunggu sesuai header Retry-After
                    wait = int(response.headers.get("Retry-After", 60))
                    logger.warning(f"[The Archivist] Rate limit (429). Menunggu {wait}s...")
                    time.sleep(min(wait, 60))
                    continue

                response.raise_for_status()
                rpc_response = response.json()

                if "error" in rpc_response:
                    err = rpc_response["error"]
                    raise RuntimeError(
                        f"MCP error [{err.get('code')}]: {err.get('message')}"
                    )

                return rpc_response.get("result", {})

            except ValueError:
                raise
            except (requests.Timeout, requests.ConnectionError) as e:
                last_error = e
                logger.warning(
                    f"[The Archivist] Attempt {attempt} gagal: {e}. "
                    f"{'Retry...' if attempt <= retries else 'Menyerah.'}"
                )
                if attempt <= retries:
                    time.sleep(attempt)
            except Exception as e:
                last_error = e
                logger.error(f"[The Archivist] Error tak terduga: {e}")
                break

        raise RuntimeError(
            f"MCP server tidak dapat dijangkau setelah {retries + 1} percobaan. "
            f"Error: {last_error}"
        )


_mcp_client: Optional[MCPClient] = None


def _get_mcp_client() -> MCPClient:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client


# ============================================================
# SEARCH CACHE — sesuai skills/the_archivist/semantic_search.md
# TTL: 5 menit untuk query identik
# ============================================================

_search_cache: Dict[str, tuple[Dict[str, Any], float]] = {}


def _get_search_cache(query_key: str) -> Optional[Dict[str, Any]]:
    if query_key in _search_cache:
        data, cached_at = _search_cache[query_key]
        if time.time() - cached_at < SEARCH_CACHE_TTL_SECONDS:
            logger.info(f"[The Archivist] Search cache HIT: '{query_key[:40]}'")
            return data
    return None


def _set_search_cache(query_key: str, data: Dict[str, Any]) -> None:
    _search_cache[query_key] = (data, time.time())


# ============================================================
# HELPERS — Auto-tagging & title formatting
# Referensi: skills/the_archivist/note_indexing.md
# ============================================================

def _auto_tag(content: str, title: str, user_tags: List[str]) -> List[str]:
    """
    Tambahkan tag otomatis berdasarkan konten catatan.
    Referensi: skills/the_archivist/note_indexing.md — Logika Auto-Tagging.
    """
    combined = (title + " " + content).lower()
    detected_tags = set(user_tags)

    for tag, keywords in AUTO_TAG_RULES.items():
        if any(kw in combined for kw in keywords):
            detected_tags.add(tag)

    # Deteksi nominal besar → keuangan
    import re
    if re.search(r'rp\s*\d{3,}|[1-9]\d{4,}', combined):
        detected_tags.add("#keuangan")

    # Pastikan semua tag ada dalam VALID_TAGS
    valid = [t for t in detected_tags if t in VALID_TAGS]

    # Minimal 1 tag wajib — fallback ke #personal
    if not valid:
        valid = ["#personal"]

    return valid


def _format_note_title(title: str, tags: List[str]) -> str:
    """
    Format judul catatan dengan prefix kategori utama.
    Referensi: skills/the_archivist/note_indexing.md — Format Judul Catatan Standar.
    """
    if title.startswith("["):
        return title   # Sudah diformat

    # Cari tag kategori utama (bukan modifier)
    primary_tags = [
        t for t in tags
        if t in NOTE_TITLE_PREFIXES
    ]

    prefix = NOTE_TITLE_PREFIXES.get(
        primary_tags[0] if primary_tags else "",
        "[INFO]",
    )
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return f"{prefix} {title} — {today}"


def _check_duplicate(title: str, existing_notes: List[Dict[str, Any]]) -> bool:
    """
    Cek kesamaan judul dengan catatan yang ada (threshold 85%).
    Algoritma: sequence matching sederhana berbasis karakter.
    Referensi: skills/the_archivist/note_indexing.md — Aturan Duplikasi.
    """
    import difflib
    title_lower = title.lower().strip()
    for note in existing_notes:
        existing_title = note.get("title", "").lower().strip()
        ratio = difflib.SequenceMatcher(None, title_lower, existing_title).ratio()
        if ratio >= DUPLICATE_SIMILARITY_THRESHOLD:
            logger.info(
                f"[The Archivist] Duplikat terdeteksi: '{title}' ≈ '{existing_title}' "
                f"(similarity={ratio:.2f})"
            )
            return True
    return False


# ============================================================
# TOOL 1: save_note
# Referensi: skills/the_archivist/note_indexing.md
#            skills/the_archivist/keep_sync_protocol.md
# ============================================================

def save_note(
    title: str,
    content: str,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Simpan catatan baru ke Google Keep via MCP.
    Termasuk: auto-tagging, format judul, dan cek duplikasi.

    Referensi keep_sync_protocol.md — Protokol Validasi:
    1. title tidak kosong ≤ 200 karakter
    2. content tidak kosong ≤ 5000 karakter
    3. Minimal 1 tag valid
    4. Cek MCP tersedia (via call — jika gagal: status=pending)

    Args:
        title: Judul catatan.
        content: Isi catatan.
        tags: Tag yang diberikan user (akan digabung dengan auto-tag).

    Returns:
        Dict hasil NoteResultSchema dengan sync_status.
    """
    # --- Validasi input (keep_sync_protocol.md: Protokol Validasi 1 & 2) ---
    if not title or not title.strip():
        raise ValueError("Judul catatan tidak boleh kosong.")
    if not content or not content.strip():
        raise ValueError("Konten catatan tidak boleh kosong.")
    if len(title) > 200:
        title = title[:200]
    if len(content) > 5000:
        content = content[:5000]
        logger.warning("[The Archivist] Konten dipotong pada 5000 karakter.")

    # --- Auto-tagging (note_indexing.md) ---
    all_tags = _auto_tag(content, title, tags or [])

    # --- Format judul standar ---
    formatted_title = _format_note_title(title, all_tags)

    # --- Cek duplikasi dalam 7 hari terakhir ---
    try:
        recent_notes_result = _get_mcp_client().call(
            tool_name="list_notes",
            arguments={"max_results": 50, "days_back": DUPLICATE_CHECK_DAYS},
        )
        recent_notes = recent_notes_result.get("notes", [])

        if _check_duplicate(formatted_title, recent_notes):
            # Duplikat: append ke catatan yang ada, bukan buat baru
            existing = next(
                (n for n in recent_notes
                 if formatted_title.lower()[:30] in n.get("title", "").lower()),
                None,
            )
            if existing:
                append_content = f"\n\n---\n[Update {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC]\n{content}"
                mcp_result = _get_mcp_client().call(
                    tool_name="update_note",
                    arguments={
                        "note_id": existing.get("id", ""),
                        "append_content": append_content,
                    },
                )
                logger.info(f"[The Archivist] Catatan di-append (duplikat dihindari): {existing.get('id')}")
                return {
                    "note_id": existing.get("id", ""),
                    "title": existing.get("title", formatted_title),
                    "content": content,
                    "tags": all_tags,
                    "created_at": existing.get("created_at", datetime.now(tz=timezone.utc).isoformat()),
                    "sync_status": "synced",
                    "action": "appended",
                }
    except RuntimeError:
        # MCP unreachable saat cek duplikat — lanjut ke save dengan status pending
        logger.warning("[The Archivist] MCP unreachable saat cek duplikat. Lanjut simpan.")
        return {
            "note_id": None,
            "title": formatted_title,
            "content": content,
            "tags": all_tags,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "sync_status": "pending",
            "message": "Catatan disimpan sementara. Akan disinkronkan saat koneksi pulih.",
        }

    # --- Simpan catatan baru via MCP ---
    try:
        mcp_result = _get_mcp_client().call(
            tool_name="save_note",
            arguments={
                "title": formatted_title,
                "content": content,
                "labels": all_tags,
            },
        )

        note_id = mcp_result.get("id", str(uuid.uuid4()))
        logger.log_agent_event(
            f"NOTE_SAVED: '{formatted_title}', tags={all_tags}",
            agent_name=AGENT_NAME,
            note_id=note_id,
        )

        return {
            "note_id": note_id,
            "title": formatted_title,
            "content": content,
            "tags": all_tags,
            "created_at": mcp_result.get(
                "created_at", datetime.now(tz=timezone.utc).isoformat()
            ),
            "sync_status": "synced",
            "url": mcp_result.get("url"),
        }

    except RuntimeError:
        # MCP Fallback: pending status (keep_sync_protocol.md — MCP Server Unreachable)
        logger.warning("[The Archivist] MCP unreachable saat menyimpan. Status: pending.")
        return {
            "note_id": None,
            "title": formatted_title,
            "content": content,
            "tags": all_tags,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "sync_status": "pending",
            "message": "Catatan disimpan sementara, belum tersinkronisasi ke Google Keep.",
        }


# ============================================================
# TOOL 2: search_notes
# Referensi: skills/the_archivist/semantic_search.md
# ============================================================

def search_notes(
    query: str,
    tags: Optional[List[str]] = None,
    days_back: Optional[int] = None,
    max_results: int = MAX_SEARCH_RESULTS,
) -> Dict[str, Any]:
    """
    Cari catatan di Google Keep via MCP.
    Mendukung pencarian keyword, tag-based, dan date-range.

    Referensi: skills/the_archivist/semantic_search.md — 4 tipe pencarian.

    Args:
        query: Kata kunci atau kalimat pencarian.
        tags: Filter berdasarkan tag (tag-based search).
        days_back: Cari catatan dalam N hari terakhir (date-range search).
        max_results: Maks hasil yang dikembalikan (default 10).

    Returns:
        Dict dengan 'results', 'query', 'total_found'.
    """
    # Cache key
    cache_key = f"{query}|{tags}|{days_back}|{max_results}"
    cached = _get_search_cache(cache_key)
    if cached:
        return cached

    # Bangun arguments sesuai tipe pencarian (semantic_search.md)
    arguments: Dict[str, Any] = {
        "max_results": min(max_results, MAX_SEARCH_RESULTS),
    }

    if query:
        arguments["query"] = query
    if tags:
        valid_filter_tags = [t for t in tags if t in VALID_TAGS]
        if valid_filter_tags:
            arguments["labels"] = valid_filter_tags
    if days_back:
        arguments["days_back"] = days_back

    try:
        mcp_result = _get_mcp_client().call(
            tool_name="search_notes",
            arguments=arguments,
        )

        notes_raw = mcp_result.get("notes", [])
        results = []
        for note in notes_raw:
            results.append({
                "note_id": note.get("id", ""),
                "title": note.get("title", ""),
                "content": note.get("text", "")[:500],  # Potong untuk efisiensi
                "tags": note.get("labels", []),
                "created_at": note.get("created_at", ""),
                "url": note.get("url"),
            })

        logger.log_agent_event(
            f"SEARCH_COMPLETED: '{query[:40]}' → {len(results)} hasil",
            agent_name=AGENT_NAME,
        )

        output = {
            "query": query,
            "results": results,
            "total_found": len(results),
        }

        # Simpan ke cache
        _set_search_cache(cache_key, output)
        return output

    except RuntimeError as e:
        logger.error(f"[The Archivist] Search gagal: {e}")
        # Fallback: kembalikan hasil kosong (semantic_search.md — Penanganan Hasil Kosong)
        return {
            "query": query,
            "results": [],
            "total_found": 0,
            "message": "Tidak ditemukan catatan yang relevan dengan pencarian ini.",
        }


# ============================================================
# TOOL 3: list_notes
# Referensi: skills/the_archivist/keep_sync_protocol.md
# ============================================================

def list_notes(
    max_results: int = 20,
    days_back: int = 7,
) -> Dict[str, Any]:
    """
    Daftar catatan terbaru dari Google Keep via MCP.
    Digunakan untuk cek duplikasi dan overview catatan.

    Args:
        max_results: Maks catatan yang dikembalikan.
        days_back: Tampilkan catatan dari N hari terakhir.

    Returns:
        Dict dengan 'notes' dan 'total'.
    """
    try:
        result = _get_mcp_client().call(
            tool_name="list_notes",
            arguments={
                "max_results": max_results,
                "days_back": days_back,
            },
        )

        notes = result.get("notes", [])
        logger.info(f"[The Archivist] list_notes: {len(notes)} catatan ditemukan.")

        return {
            "notes": [
                {
                    "note_id": n.get("id", ""),
                    "title": n.get("title", ""),
                    "tags": n.get("labels", []),
                    "created_at": n.get("created_at", ""),
                }
                for n in notes
            ],
            "total": len(notes),
        }

    except RuntimeError as e:
        logger.error(f"[The Archivist] list_notes gagal: {e}")
        return {"notes": [], "total": 0, "error": str(e)}
