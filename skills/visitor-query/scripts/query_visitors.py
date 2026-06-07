#!/usr/bin/env python3
"""Visitor query tool for Hermes skill integration.

Usage:
    python3 query_visitors.py --stats [--period day|week|month]
    python3 query_visitors.py --peak-hours
    python3 query_visitors.py --visitor <plate_or_name>
    python3 query_visitors.py --top [N]
    python3 query_visitors.py --recent [N]
"""
import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path("/home/rimuru/VoiceAgent/data/visitors.db")


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS visit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plate TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            purpose TEXT NOT NULL DEFAULT '',
            visited_at TEXT NOT NULL
        )
    """)
    return conn


def stats(period: str):
    now = datetime.now()
    if period == "day":
        since = (now - timedelta(days=1)).isoformat(timespec="seconds")
        label = "今天"
    elif period == "week":
        since = (now - timedelta(weeks=1)).isoformat(timespec="seconds")
        label = "本周"
    else:
        since = (now - timedelta(days=30)).isoformat(timespec="seconds")
        label = "本月"

    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT plate), COUNT(*) FROM visit_log "
            "WHERE visited_at >= ?", (since,)
        ).fetchone()
    return {"period": label, "unique_vehicles": row[0], "total_visits": row[1]}


def peak_hours():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT substr(visited_at, 12, 2) as hour, COUNT(*) as cnt "
            "FROM visit_log GROUP BY hour ORDER BY cnt DESC"
        ).fetchall()
    return [{"hour": f"{r[0]}:00", "visits": r[1]} for r in rows]


def visitor_info(query: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT plate, company, purpose, phone, name, visit_count, "
            "first_seen, last_seen FROM visitors "
            "WHERE plate LIKE ? OR name LIKE ?",
            (f"%{query}%", f"%{query}%")
        ).fetchone()
    if not row:
        return {"error": f"未找到匹配 '{query}' 的访客"}
    cols = ["plate", "company", "purpose", "phone", "name",
            "visit_count", "first_seen", "last_seen"]
    return dict(zip(cols, row))


def top_visitors(n: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT plate, name, company, visit_count, last_seen "
            "FROM visitors ORDER BY visit_count DESC LIMIT ?", (n,)
        ).fetchall()
    return [{"plate": r[0], "name": r[1], "company": r[2],
             "visit_count": r[3], "last_seen": r[4]} for r in rows]


def recent_visits(n: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT plate, company, purpose, visited_at "
            "FROM visit_log ORDER BY visited_at DESC LIMIT ?", (n,)
        ).fetchall()
    return [{"plate": r[0], "company": r[1], "purpose": r[2],
             "visited_at": r[3]} for r in rows]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--period", default="week")
    parser.add_argument("--peak-hours", action="store_true")
    parser.add_argument("--visitor", type=str)
    parser.add_argument("--top", nargs="?", const=10, type=int)
    parser.add_argument("--recent", nargs="?", const=10, type=int)
    args = parser.parse_args()

    if args.stats:
        result = stats(args.period)
    elif args.peak_hours:
        result = peak_hours()
    elif args.visitor:
        result = visitor_info(args.visitor)
    elif args.top is not None:
        result = top_visitors(args.top)
    elif args.recent is not None:
        result = recent_visits(args.recent)
    else:
        parser.print_help()
        exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))
