from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class StateStore:
    def __init__(self, path: str) -> None:
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rule_state (
                    steam_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    warnings_sent INTEGER NOT NULL DEFAULT 0,
                    last_warning_ts REAL,
                    last_action_ts REAL,
                    PRIMARY KEY (steam_id, rule_id)
                )
                """
            )

    def get(self, steam_id: str, rule_id: str) -> dict[str, float | int | None]:
        with self.lock:
            row = self.conn.execute(
                "SELECT warnings_sent, last_warning_ts, last_action_ts FROM rule_state WHERE steam_id=? AND rule_id=?",
                (steam_id, rule_id),
            ).fetchone()
        if not row:
            return {"warnings_sent": 0, "last_warning_ts": None, "last_action_ts": None}
        return dict(row)

    def record_warning(self, steam_id: str, rule_id: str) -> None:
        now = time.time()
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO rule_state (steam_id, rule_id, warnings_sent, last_warning_ts)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(steam_id, rule_id) DO UPDATE SET
                    warnings_sent = warnings_sent + 1,
                    last_warning_ts = excluded.last_warning_ts
                """,
                (steam_id, rule_id, now),
            )

    def record_action(self, steam_id: str, rule_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO rule_state (steam_id, rule_id, warnings_sent, last_action_ts)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(steam_id, rule_id) DO UPDATE SET
                    last_action_ts = excluded.last_action_ts
                """,
                (steam_id, rule_id, time.time()),
            )

    def clear_absent_players(self, present_steam_ids: set[str]) -> int:
        with self.lock, self.conn:
            if not present_steam_ids:
                cur = self.conn.execute("DELETE FROM rule_state")
                return cur.rowcount
            placeholders = ",".join("?" for _ in present_steam_ids)
            cur = self.conn.execute(
                f"DELETE FROM rule_state WHERE steam_id NOT IN ({placeholders})",
                tuple(present_steam_ids),
            )
            return cur.rowcount

    def clear_player(self, steam_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM rule_state WHERE steam_id=?", (steam_id,))
