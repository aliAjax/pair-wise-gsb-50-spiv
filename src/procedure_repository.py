"""陈述申辩、证据与听证程序记录的 SQLite 持久化，与案件记录分表保存。"""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProcedureRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS procedures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    submitted_day INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    details TEXT NOT NULL DEFAULT '{}',
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_procedures_record ON procedures(record_id, id);
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        item["details"] = json.loads(item["details"])
        return item

    def add(self, record_id: int, kind: str, status: str, submitted_day: int, content: str, note: str, details: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO procedures(record_id,kind,status,submitted_day,content,note,details,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (record_id, kind, status, int(submitted_day), content, note, json.dumps(details or {}, ensure_ascii=False, sort_keys=True), actor_id, _now()),
            )
            row = connection.execute("SELECT * FROM procedures WHERE id=?", (int(cursor.lastrowid),)).fetchone()
        return self._row(row)

    def list_for_record(self, record_id: int) -> List[Dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM procedures WHERE record_id=? ORDER BY id", (record_id,)).fetchall()
        return [self._row(row) for row in rows]
