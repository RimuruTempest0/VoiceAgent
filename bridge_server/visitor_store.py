"""Lightweight SQLite visitor store.

Source of truth for visitor records. Hermes USER.md is regenerated from here
after each upsert so the LLM always sees a clean, deduplicated memory.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

DB_PATH = Path(settings.data_dir) / "visitors.db"
HERMES_MEMORY_PATH = Path(settings.skill_path).parent.parent.parent / "memories" / "USER.md"

_MEMORY_HEADER = (
    "User prefers to keep existing safety/redundancy mechanisms "
    "(e.g., dual file checkpoint + DB check) when running batch inference pipelines. "
    "If asked to simplify and then says '改回上一版吧' or similar revert signal, "
    "revert immediately — they considered the tradeoff and want the original pattern.\n§\n"
)


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS visitors (
            plate TEXT PRIMARY KEY,
            company TEXT NOT NULL DEFAULT '',
            purpose TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            visit_count INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def sync_hermes_memory() -> None:
    """Public entry point — call on startup or after manual DB changes."""
    _sync_hermes_memory()


def upsert_visitor(visitor: dict) -> None:
    """Insert or update a visitor record keyed by license plate."""
    plate = (visitor.get("plate") or "").strip()
    if not plate:
        logger.warning("upsert_visitor: missing plate, skipping")
        return
    company = (visitor.get("company") or "").strip()
    purpose = (visitor.get("purpose") or "").strip()
    phone = (visitor.get("phone") or "").strip()
    now = datetime.now().isoformat(timespec="seconds")

    with _get_conn() as conn:
        cur = conn.execute("SELECT visit_count FROM visitors WHERE plate=?", (plate,))
        row = cur.fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO visitors(plate, company, purpose, phone, visit_count, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)",
                (plate, company, purpose, phone, now, now),
            )
            logger.info("New visitor: plate=%s company=%s", plate, company)
        else:
            conn.execute(
                "UPDATE visitors SET company=?, purpose=?, phone=?, "
                "visit_count=visit_count+1, last_seen=? WHERE plate=?",
                (company, purpose, phone, now, plate),
            )
            logger.info("Return visitor: plate=%s (visit #%d)", plate, row[0] + 1)
        conn.commit()

    _sync_hermes_memory()


def list_visitors() -> list[dict]:
    """Return all visitors ordered by most recent visit."""
    with _get_conn() as conn:
        cur = conn.execute(
            "SELECT plate, company, purpose, phone, visit_count, first_seen, last_seen "
            "FROM visitors ORDER BY last_seen DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _sync_hermes_memory() -> None:
    """Regenerate Hermes USER.md from the SQLite source of truth."""
    try:
        rows = list_visitors()
        lines = [
            f"车牌: {r['plate']}; 单位: {r['company']} ({r['purpose']}); 手机: {r['phone']}"
            for r in rows
        ]
        content = _MEMORY_HEADER + "\n".join(lines) + ("\n" if lines else "")
        HERMES_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        HERMES_MEMORY_PATH.write_text(content, encoding="utf-8")
        logger.info("Hermes memory synced: %d visitor(s)", len(rows))
    except Exception:
        logger.exception("Failed to sync Hermes memory")
