"""Append-only local collection runs, including failed attempts."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing

from goofish_z.core.paths import runtime_data_path


def connect():
    path = runtime_data_path("prices.db")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    path.chmod(0o600)
    db = sqlite3.connect(path, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS price_runs (id TEXT PRIMARY KEY, query TEXT NOT NULL, collected_at TEXT NOT NULL, payload TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS price_runs_query_time ON price_runs(query,collected_at)")
    return db


def save(result: dict):
    with closing(connect()) as db, db:
        db.execute("INSERT INTO price_runs VALUES (?,?,?,?)", (result["id"],result["query"],result["collected_at"],json.dumps(result,ensure_ascii=False)))


def history(query: str, limit: int = 20) -> list[dict]:
    if not query.strip() or not 1 <= limit <= 100:
        raise ValueError("请指定关键词，记录条数须为 1 到 100")
    with closing(connect()) as db, db:
        rows = db.execute("SELECT payload FROM price_runs WHERE query=? ORDER BY collected_at DESC, rowid DESC LIMIT ?", (query.strip(),limit)).fetchall()
    return [json.loads(row[0]) for row in rows]
