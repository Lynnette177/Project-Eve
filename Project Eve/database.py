import sqlite3
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional


class EveDatabase:
    """SQLite storage layer for Project Eve chat messages."""

    VALID_ROLES = {"user", "assistant"}
    VALID_DIRECTIONS = {"incoming", "outgoing"}
    VALID_MESSAGE_TYPES = {"text", "image", "file", "voice", "video"}
    VALID_TASK_STATUSES = {"pending_info", "completed", "failed", "cancelled"}
    ACTIVE_TASK_STATUSES = {"pending_info"}

    def __init__(self, db_path: str | Path | None = None, initialize: bool = True):
        project_root = Path(__file__).resolve().parent
        self.db_path = Path(db_path) if db_path else project_root / "Memories.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")

        if initialize:
            self.initialize_schema()

    def close(self) -> None:
        self.conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.conn:
            return self.conn.execute(sql, tuple(params))

    def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def initialize_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                direction TEXT NOT NULL CHECK (direction IN ('incoming', 'outgoing')),
                message_type TEXT NOT NULL CHECK (message_type IN ('text', 'image', 'file', 'voice', 'video')),
                content TEXT,
                media_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS message_stats (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            INSERT OR IGNORE INTO message_stats (key, value, updated_at)
            VALUES ('received_count', 0, datetime('now'));

            INSERT OR IGNORE INTO message_stats (key, value, updated_at)
            VALUES ('sent_count', 0, datetime('now'));

            CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);
            CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role);
            CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(message_type);
            CREATE INDEX IF NOT EXISTS idx_messages_media_id ON messages(media_id);

            CREATE TABLE IF NOT EXISTS task_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL CHECK (status IN ('pending_info', 'completed', 'failed', 'cancelled')),
                task_name TEXT NOT NULL,
                task_description TEXT NOT NULL,
                task_info TEXT,
                last_question TEXT,
                result TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_message_at TEXT,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_task_sessions_status ON task_sessions(status);
            CREATE INDEX IF NOT EXISTS idx_task_sessions_updated_at ON task_sessions(updated_at);
            CREATE INDEX IF NOT EXISTS idx_task_sessions_last_message_at ON task_sessions(last_message_at);

            CREATE TABLE IF NOT EXISTS event_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                happened_at TEXT,
                valid_until TEXT,
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'expired', 'archived', 'forgotten', 'deleted')),
                importance REAL NOT NULL DEFAULT 1.0,
                emotional_weight REAL NOT NULL DEFAULT 1.0,
                recall_count INTEGER NOT NULL DEFAULT 1,
                mention_count INTEGER NOT NULL DEFAULT 1,
                forget_weight REAL NOT NULL DEFAULT 1.0,
                source_message_ids TEXT,
                last_recalled_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_event_memories_status ON event_memories(status);
            CREATE INDEX IF NOT EXISTS idx_event_memories_category ON event_memories(category);
            CREATE INDEX IF NOT EXISTS idx_event_memories_happened_at ON event_memories(happened_at);
            CREATE INDEX IF NOT EXISTS idx_event_memories_valid_until ON event_memories(valid_until);
            CREATE INDEX IF NOT EXISTS idx_event_memories_forget_weight ON event_memories(forget_weight);

            CREATE TABLE IF NOT EXISTS core_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_key TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 3,
                confidence REAL NOT NULL DEFAULT 1.0,
                source TEXT NOT NULL DEFAULT 'memory_agent',
                mention_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_core_memories_category ON core_memories(category);
            CREATE INDEX IF NOT EXISTS idx_core_memories_priority ON core_memories(priority);

            CREATE TABLE IF NOT EXISTS proactive_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('planned', 'skipped', 'sent', 'task_started', 'failed')),
                action_type TEXT NOT NULL DEFAULT 'none',
                reason TEXT,
                decision TEXT,
                task_id INTEGER,
                sent_messages TEXT,
                metadata TEXT,
                scheduled_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_proactive_events_created_at ON proactive_events(created_at);
            CREATE INDEX IF NOT EXISTS idx_proactive_events_status ON proactive_events(status);
            CREATE INDEX IF NOT EXISTS idx_proactive_events_event_type ON proactive_events(event_type);

            CREATE TABLE IF NOT EXISTS proactive_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            );

            """
        )
        self.conn.commit()

    @staticmethod
    def now() -> str:
        china_tz = timezone(timedelta(hours=8))
        return datetime.now(china_tz).isoformat()

    @staticmethod
    def to_json(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def from_json(value: str | None, default: Any = None) -> Any:
        if value is None:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default

    def _decode_task_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        row["task_info"] = self.from_json(row.get("task_info"), default={})
        row["task_state"] = row["task_info"]  # backward-compatible alias
        row["result"] = self.from_json(row.get("result"), default=None)
        return row

    def _decode_event_memory_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        row["source_message_ids"] = self.from_json(row.get("source_message_ids"), default=[])
        return row

    @staticmethod
    def merge_dict(base: dict[str, Any] | None, patch: dict[str, Any] | None) -> dict[str, Any]:
        """Recursively merge patch into base and return a new dict."""
        merged = dict(base or {})
        for key, value in (patch or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = EveDatabase.merge_dict(merged[key], value)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def normalize_text(text: str) -> str:
        text = (text or "").lower().strip()
        return re.sub(r"\s+", "", text)

    @staticmethod
    def text_similarity(first: str, second: str) -> float:
        first_norm = EveDatabase.normalize_text(first)
        second_norm = EveDatabase.normalize_text(second)
        if not first_norm or not second_norm:
            return 0.0
        if first_norm == second_norm:
            return 1.0
        if first_norm in second_norm or second_norm in first_norm:
            return min(len(first_norm), len(second_norm)) / max(len(first_norm), len(second_norm))

        first_chars = set(first_norm)
        second_chars = set(second_norm)
        if not first_chars or not second_chars:
            return 0.0
        intersection = len(first_chars & second_chars)
        jaccard = intersection / len(first_chars | second_chars)
        coverage = intersection / min(len(first_chars), len(second_chars))
        return max(jaccard, coverage * 0.8)


    def add_message(
        self,
        role: str,
        direction: str,
        message_type: str,
        content: str | None = None,
        media_id: str | None = None,
        created_at: str | None = None,
    ) -> int:
        if role not in self.VALID_ROLES:
            raise ValueError(f"Invalid role: {role}")
        if direction not in self.VALID_DIRECTIONS:
            raise ValueError(f"Invalid direction: {direction}")
        if message_type not in self.VALID_MESSAGE_TYPES:
            raise ValueError(f"Invalid message_type: {message_type}")

        cursor = self.execute(
            """
            INSERT INTO messages
            (role, direction, message_type, content, media_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (role, direction, message_type, content, media_id, created_at or self.now()),
        )
        return int(cursor.lastrowid)

    def add_user_message(
        self,
        message_type: str,
        content: str | None = None,
        media_id: str | None = None,
        created_at: str | None = None,
    ) -> int:
        return self.add_message(
            role="user",
            direction="incoming",
            message_type=message_type,
            content=content,
            media_id=media_id,
            created_at=created_at,
        )

    def add_assistant_message(
        self,
        message_type: str,
        content: str | None = None,
        media_id: str | None = None,
        created_at: str | None = None,
    ) -> int:
        return self.add_message(
            role="assistant",
            direction="outgoing",
            message_type=message_type,
            content=content,
            media_id=media_id,
            created_at=created_at,
        )

    def get_message(self, message_id: int) -> Optional[dict[str, Any]]:
        return self.fetch_one("SELECT * FROM messages WHERE id = ?", (message_id,))

    def get_recent_messages(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.fetch_all(
            """
            SELECT id, role, direction, message_type, content, media_id, created_at
            FROM messages
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return list(reversed(rows))

    def get_latest_completed_turn_messages(self) -> list[dict[str, Any]]:
        """Return the latest completed conversation turn, starting with assistant messages.

        A completed turn is the newest continuous block of assistant messages followed by
        user messages. If the latest message is from the user and has not been answered
        yet, that pending user block is skipped first.
        """
        rows = self.fetch_all(
            """
            SELECT id, role, direction, message_type, content, media_id, created_at
            FROM messages
            ORDER BY created_at DESC, id DESC
            """
        )
        if not rows:
            return []

        index = 0

        while index < len(rows) and rows[index]["role"] == "user":
            index += 1

        if index >= len(rows) or rows[index]["role"] != "assistant":
            return []

        turn_rows = []

        while index < len(rows) and rows[index]["role"] == "assistant":
            turn_rows.append(rows[index])
            index += 1

        while index < len(rows) and rows[index]["role"] == "user":
            turn_rows.append(rows[index])
            index += 1

        return list(reversed(turn_rows))

    def get_messages_between(self, start_at: str, end_at: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT id, role, direction, message_type, content, media_id, created_at
            FROM messages
            WHERE created_at >= ? AND created_at < ?
            ORDER BY created_at ASC, id ASC
            """,
            (start_at, end_at),
        )

    def delete_message(self, message_id: int) -> None:
        self.execute("DELETE FROM messages WHERE id = ?", (message_id,))

    def increment_stat(self, key: str, amount: int = 1) -> int:
        self.execute(
            """
            INSERT INTO message_stats (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = value + excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, amount, self.now()),
        )
        return self.get_stat(key)

    def increment_received_count(self, amount: int = 1) -> int:
        return self.increment_stat("received_count", amount)

    def increment_sent_count(self, amount: int = 1) -> int:
        return self.increment_stat("sent_count", amount)

    def get_stat(self, key: str, default: int = 0) -> int:
        row = self.fetch_one("SELECT value FROM message_stats WHERE key = ?", (key,))
        return int(row["value"]) if row else default

    def get_message_stats(self) -> dict[str, int]:
        rows = self.fetch_all("SELECT key, value FROM message_stats")
        stats = {row["key"]: int(row["value"]) for row in rows}
        stats.setdefault("received_count", 0)
        stats.setdefault("sent_count", 0)
        return stats

    def reset_message_stats(self) -> None:
        now = self.now()
        self.execute(
            """
            INSERT INTO message_stats (key, value, updated_at)
            VALUES ('received_count', 0, ?), ('sent_count', 0, ?)
            ON CONFLICT(key) DO UPDATE SET value = 0, updated_at = excluded.updated_at
            """,
            (now, now),
        )

    def create_task_session(
        self,
        task_name: str,
        task_description: str,
        task_info: dict[str, Any] | None = None,
        status: str = "pending_info",
        last_question: str | None = None,
        last_message_at: str | None = None,
    ) -> int:
        if status not in self.VALID_TASK_STATUSES:
            raise ValueError(f"Invalid task status: {status}")
        now = self.now()
        cursor = self.execute(
            """
            INSERT INTO task_sessions
            (status, task_name, task_description, task_info, last_question, created_at, updated_at, last_message_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                status,
                task_name,
                task_description,
                self.to_json(task_info or {}),
                last_question,
                now,
                now,
                last_message_at or now,
            ),
        )
        return int(cursor.lastrowid)

    def get_task_session(self, task_id: int) -> dict[str, Any] | None:
        row = self.fetch_one("SELECT * FROM task_sessions WHERE id = ?", (task_id,))
        return self._decode_task_row(row)

    def get_active_task_session(self) -> dict[str, Any] | None:
        placeholders = ", ".join("?" for _ in self.ACTIVE_TASK_STATUSES)
        row = self.fetch_one(
            f"""
            SELECT * FROM task_sessions
            WHERE status IN ({placeholders})
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            tuple(self.ACTIVE_TASK_STATUSES),
        )
        return self._decode_task_row(row)

    def list_active_task_sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in self.ACTIVE_TASK_STATUSES)
        rows = self.fetch_all(
            f"""
            SELECT * FROM task_sessions
            WHERE status IN ({placeholders})
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (*tuple(self.ACTIVE_TASK_STATUSES), limit),
        )
        return [self._decode_task_row(row) for row in rows]

    def list_task_sessions(
        self,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if status:
            rows = self.fetch_all(
                """
                SELECT * FROM task_sessions
                WHERE status = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            rows = self.fetch_all(
                """
                SELECT * FROM task_sessions
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [self._decode_task_row(row) for row in rows]

    def update_task_session(
        self,
        task_id: int,
        status: str | None = None,
        task_info: dict[str, Any] | None = None,
        last_question: str | None = None,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
        touch_last_message: bool = True,
    ) -> None:
        current = self.get_task_session(task_id)
        if not current:
            raise ValueError(f"Task session not found: {task_id}")

        next_status = status or current["status"]
        if next_status not in self.VALID_TASK_STATUSES:
            raise ValueError(f"Invalid task status: {next_status}")
        now = self.now()
        completed_at = now if next_status in {"completed", "failed", "cancelled"} else current.get("completed_at")

        self.execute(
            """
            UPDATE task_sessions
            SET status = ?,
                task_info = ?,
                last_question = ?,
                result = ?,
                error_message = ?,
                updated_at = ?,
                last_message_at = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (
                next_status,
                self.to_json(task_info if task_info is not None else current.get("task_info", {})),
                last_question if last_question is not None else current.get("last_question"),
                self.to_json(result) if result is not None else self.to_json(current.get("result")),
                error_message if error_message is not None else current.get("error_message"),
                now,
                now if touch_last_message else current.get("last_message_at"),
                completed_at,
                task_id,
            ),
        )

    def update_task_info(
        self,
        task_id: int,
        task_info: dict[str, Any],
        merge: bool = True,
    ) -> dict[str, Any]:
        current = self.get_task_session(task_id)
        if not current:
            raise ValueError(f"Task session not found: {task_id}")

        next_state = (
            self.merge_dict(current.get("task_info", {}), task_info)
            if merge
            else task_info
        )
        self.update_task_session(task_id=task_id, task_info=next_state)
        return next_state

    def replace_task_info(self, task_id: int, task_info: dict[str, Any]) -> dict[str, Any]:
        return self.update_task_info(task_id=task_id, task_info=task_info, merge=False)

    def update_task_state(self, task_id: int, task_state: dict[str, Any], merge: bool = True) -> dict[str, Any]:
        """Backward-compatible alias for update_task_info."""
        return self.update_task_info(task_id=task_id, task_info=task_state, merge=merge)

    def replace_task_state(self, task_id: int, task_state: dict[str, Any]) -> dict[str, Any]:
        """Backward-compatible alias for replace_task_info."""
        return self.replace_task_info(task_id=task_id, task_info=task_state)

    def change_task_status(self, task_id: int, status: str) -> None:
        self.update_task_session(task_id=task_id, status=status)

    def complete_task_session(self, task_id: int, result: dict[str, Any] | None = None) -> None:
        self.update_task_session(task_id=task_id, status="completed", result=result)

    def fail_task_session(self, task_id: int, error_message: str | None = None, result: dict[str, Any] | None = None) -> None:
        self.update_task_session(task_id=task_id, status="failed", error_message=error_message, result=result)

    def cancel_task_session(self, task_id: int, reason: str | None = None) -> None:
        self.update_task_session(task_id=task_id, status="cancelled", error_message=reason)

    def delete_task_session(self, task_id: int) -> None:
        self.execute("DELETE FROM task_sessions WHERE id = ?", (task_id,))


    def add_core_memory(
        self,
        memory_key: str,
        category: str,
        content: str,
        priority: int = 3,
        confidence: float = 1.0,
        source: str = "memory_agent",
    ) -> int:
        now = self.now()
        priority = max(1, min(5, int(priority)))
        confidence = max(0.0, min(1.0, float(confidence)))
        self.execute(
            """
            INSERT INTO core_memories
            (memory_key, category, content, priority, confidence, source, mention_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (memory_key, category, content, priority, confidence, source, now, now),
        )
        row = self.fetch_one("SELECT id FROM core_memories WHERE memory_key = ?", (memory_key,))
        return int(row["id"])

    def upsert_core_memory(
        self,
        memory_key: str,
        category: str,
        content: str,
        priority: int = 3,
        confidence: float = 1.0,
        source: str = "memory_agent",
        reinforce: bool = True,
    ) -> dict[str, Any]:
        now = self.now()
        priority = max(1, min(5, int(priority)))
        confidence = max(0.0, min(1.0, float(confidence)))
        existing = self.get_core_memory_by_key(memory_key)
        if existing:
            self.execute(
                """
                UPDATE core_memories
                SET category = ?, content = ?, priority = ?, confidence = ?, source = ?,
                    mention_count = mention_count + ?, updated_at = ?
                WHERE memory_key = ?
                """,
                (category, content, priority, confidence, source, 1 if reinforce else 0, now, memory_key),
            )
            return {"action": "updated", "memory": self.get_core_memory_by_key(memory_key)}

        memory_id = self.add_core_memory(memory_key, category, content, priority, confidence, source)
        return {"action": "created", "memory": self.get_core_memory(memory_id)}

    def get_core_memory(self, memory_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM core_memories WHERE id = ?", (memory_id,))

    def get_core_memory_by_key(self, memory_key: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM core_memories WHERE memory_key = ?", (memory_key,))

    def list_core_memories(
        self,
        category: str | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        if category:
            return self.fetch_all(
                """
                SELECT * FROM core_memories
                WHERE category = ?
                ORDER BY priority DESC, confidence DESC, updated_at DESC
                LIMIT ?
                """,
                (category, limit),
            )
        return self.fetch_all(
            """
            SELECT * FROM core_memories
            ORDER BY priority DESC, confidence DESC, updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )

    def search_core_memories(
        self,
        query: str,
        category: str | None = None,
        limit: int = 8,
        min_similarity: float = 0.35,
    ) -> list[dict[str, Any]]:
        candidates = self.list_core_memories(category=category, limit=200)
        scored = []
        for memory in candidates:
            score = max(
                self.text_similarity(query, memory.get("content", "")),
                self.text_similarity(query, memory.get("memory_key", "")),
                self.text_similarity(query, memory.get("category", "")),
            )
            if score >= min_similarity:
                memory["similarity"] = score
                scored.append(memory)
        scored.sort(key=lambda item: (item["similarity"], item.get("priority", 0), item.get("confidence", 0)), reverse=True)
        return scored[:limit]

    def update_core_memory(
        self,
        memory_id: int,
        content: str | None = None,
        category: str | None = None,
        memory_key: str | None = None,
        priority: int | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        current = self.get_core_memory(memory_id)
        if not current:
            raise ValueError(f"Core memory not found: {memory_id}")
        next_priority = current["priority"] if priority is None else max(1, min(5, int(priority)))
        next_confidence = current["confidence"] if confidence is None else max(0.0, min(1.0, float(confidence)))
        self.execute(
            """
            UPDATE core_memories
            SET memory_key = ?, category = ?, content = ?, priority = ?, confidence = ?,
                mention_count = mention_count + 1, updated_at = ?
            WHERE id = ?
            """,
            (
                memory_key or current["memory_key"],
                category or current["category"],
                content or current["content"],
                next_priority,
                next_confidence,
                self.now(),
                memory_id,
            ),
        )
        return self.get_core_memory(memory_id)

    def reinforce_core_memory(self, memory_id: int) -> dict[str, Any]:
        self.execute(
            """
            UPDATE core_memories
            SET mention_count = mention_count + 1, updated_at = ?
            WHERE id = ?
            """,
            (self.now(), memory_id),
        )
        return self.get_core_memory(memory_id)

    def delete_core_memory(self, memory_id: int) -> None:
        self.execute("DELETE FROM core_memories WHERE id = ?", (memory_id,))

    def add_event_memory(
        self,
        content: str,
        category: str = "general",
        happened_at: str | None = None,
        valid_until: str | None = None,
        importance: float = 1.0,
        emotional_weight: float = 1.0,
        forget_weight: float | None = None,
        source_message_ids: list[int] | None = None,
        status: str = "active",
    ) -> int:
        now = self.now()
        if forget_weight is None:
            forget_weight = max(0.1, float(importance) + float(emotional_weight) * 0.5)
        cursor = self.execute(
            """
            INSERT INTO event_memories
            (content, category, happened_at, valid_until, status, importance, emotional_weight,
             recall_count, mention_count, forget_weight, source_message_ids, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?)
            """,
            (
                content,
                category,
                happened_at,
                valid_until,
                status,
                float(importance),
                float(emotional_weight),
                float(forget_weight),
                self.to_json(source_message_ids or []),
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def get_event_memory(self, memory_id: int) -> dict[str, Any] | None:
        row = self.fetch_one("SELECT * FROM event_memories WHERE id = ?", (memory_id,))
        return self._decode_event_memory_row(row)

    def list_event_memories(
        self,
        status: str | None = None,
        category: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if category:
            clauses.append("category = ?")
            params.append(category)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.fetch_all(
            f"""
            SELECT * FROM event_memories
            {where}
            ORDER BY forget_weight DESC, updated_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [self._decode_event_memory_row(row) for row in rows]

    def search_event_memories(
        self,
        query: str,
        category: str | None = None,
        status: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.35,
    ) -> list[dict[str, Any]]:
        candidates = self.list_event_memories(status=status, category=category, limit=300)
        scored = []
        for memory in candidates:
            if status is None and memory.get("status") == "deleted":
                continue
            score = self.text_similarity(query, memory.get("content", ""))
            if score >= min_similarity:
                memory["similarity"] = score
                scored.append(memory)
        scored.sort(key=lambda item: (item["similarity"], item.get("forget_weight", 0)), reverse=True)
        return scored[:limit]

    def update_event_memory(
        self,
        memory_id: int,
        content: str | None = None,
        category: str | None = None,
        happened_at: str | None = None,
        valid_until: str | None = None,
        status: str | None = None,
        importance: float | None = None,
        emotional_weight: float | None = None,
        forget_weight: float | None = None,
        source_message_ids: list[int] | None = None,
    ) -> None:
        current = self.get_event_memory(memory_id)
        if not current:
            raise ValueError(f"Event memory not found: {memory_id}")
        self.execute(
            """
            UPDATE event_memories
            SET content = ?, category = ?, happened_at = ?, valid_until = ?, status = ?,
                importance = ?, emotional_weight = ?, forget_weight = ?, source_message_ids = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                content if content is not None else current["content"],
                category if category is not None else current["category"],
                happened_at if happened_at is not None else current.get("happened_at"),
                valid_until if valid_until is not None else current.get("valid_until"),
                status if status is not None else current["status"],
                float(importance) if importance is not None else current["importance"],
                float(emotional_weight) if emotional_weight is not None else current["emotional_weight"],
                float(forget_weight) if forget_weight is not None else current["forget_weight"],
                self.to_json(source_message_ids) if source_message_ids is not None else self.to_json(current.get("source_message_ids", [])),
                self.now(),
                memory_id,
            ),
        )

    def reinforce_event_memory(
        self,
        memory_id: int,
        importance_delta: float = 0.3,
        emotional_delta: float = 0.1,
        forget_delta: float = 0.8,
        content: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_event_memory(memory_id)
        if not current:
            raise ValueError(f"Event memory not found: {memory_id}")
        self.execute(
            """
            UPDATE event_memories
            SET content = ?,
                importance = importance + ?,
                emotional_weight = emotional_weight + ?,
                forget_weight = forget_weight + ?,
                mention_count = mention_count + 1,
                recall_count = recall_count + 1,
                last_recalled_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                content or current["content"],
                float(importance_delta),
                float(emotional_delta),
                float(forget_delta),
                self.now(),
                self.now(),
                memory_id,
            ),
        )
        return self.get_event_memory(memory_id)

    def save_event_memory_dedup(
        self,
        content: str,
        category: str = "general",
        happened_at: str | None = None,
        valid_until: str | None = None,
        importance: float = 1.0,
        emotional_weight: float = 1.0,
        source_message_ids: list[int] | None = None,
        duplicate_threshold: float = 0.72,
    ) -> dict[str, Any]:
        matches = self.search_event_memories(
            query=content,
            category=category,
            status=None,
            limit=1,
            min_similarity=duplicate_threshold,
        )
        if matches:
            reinforced = self.reinforce_event_memory(
                matches[0]["id"],
                importance_delta=max(0.2, importance * 0.3),
                emotional_delta=max(0.0, emotional_weight * 0.2),
                forget_delta=max(0.5, importance * 0.5),
                content=matches[0]["content"],
            )
            return {"action": "reinforced", "memory": reinforced, "matched_similarity": matches[0]["similarity"]}

        memory_id = self.add_event_memory(
            content=content,
            category=category,
            happened_at=happened_at,
            valid_until=valid_until,
            importance=importance,
            emotional_weight=emotional_weight,
            source_message_ids=source_message_ids,
        )
        return {"action": "created", "memory": self.get_event_memory(memory_id), "matched_similarity": None}

    def mark_event_memory_recalled(self, memory_id: int, weight_bonus: float = 0.2) -> None:
        self.execute(
            """
            UPDATE event_memories
            SET recall_count = recall_count + 1,
                forget_weight = forget_weight + ?,
                last_recalled_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (float(weight_bonus), self.now(), self.now(), memory_id),
        )

    def decay_event_memories(
        self,
        decay: float = 0.05,
        expire_below: float = 0.8,
        archive_below: float = 0.35,
        forget_below: float = 0.12,
        delete_below: float = 0.03,
        event_category_decay: float = 0.12,
    ) -> dict[str, int]:
        now = self.now()
        # category='event' 的记忆遗忘更快
        self.execute(
            """
            UPDATE event_memories
            SET forget_weight = MAX(0, forget_weight - ?), updated_at = ?
            WHERE status IN ('active', 'expired', 'archived', 'forgotten')
              AND category = 'event'
            """,
            (float(event_category_decay), now),
        )
        self.execute(
            """
            UPDATE event_memories
            SET forget_weight = MAX(0, forget_weight - ?), updated_at = ?
            WHERE status IN ('active', 'expired', 'archived', 'forgotten')
              AND category != 'event'
            """,
            (float(decay), now),
        )
        expired = self.execute(
            """
            UPDATE event_memories
            SET status = 'expired', updated_at = ?
            WHERE status = 'active' AND forget_weight < ?
            """,
            (now, float(expire_below)),
        ).rowcount
        archived = self.execute(
            """
            UPDATE event_memories
            SET status = 'archived', updated_at = ?
            WHERE status = 'expired' AND forget_weight < ?
            """,
            (now, float(archive_below)),
        ).rowcount
        forgotten = self.execute(
            """
            UPDATE event_memories
            SET status = 'forgotten', updated_at = ?
            WHERE status = 'archived' AND forget_weight < ?
            """,
            (now, float(forget_below)),
        ).rowcount
        deleted = self.execute(
            """
            UPDATE event_memories
            SET status = 'deleted', updated_at = ?
            WHERE status = 'forgotten' AND forget_weight < ?
            """,
            (now, float(delete_below)),
        ).rowcount
        return {"expired": expired, "archived": archived, "forgotten": forgotten, "deleted": deleted}

    def add_proactive_event(
        self,
        event_type: str,
        status: str = "planned",
        action_type: str = "none",
        reason: str | None = None,
        decision: dict[str, Any] | None = None,
        task_id: int | None = None,
        sent_messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        scheduled_at: str | None = None,
    ) -> int:
        now = self.now()
        cursor = self.execute(
            """
            INSERT INTO proactive_events
            (event_type, status, action_type, reason, decision, task_id, sent_messages, metadata, scheduled_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                status,
                action_type,
                reason,
                self.to_json(decision),
                task_id,
                self.to_json(sent_messages or []),
                self.to_json(metadata or {}),
                scheduled_at,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def update_proactive_event(
        self,
        event_id: int,
        status: str | None = None,
        action_type: str | None = None,
        reason: str | None = None,
        decision: dict[str, Any] | None = None,
        task_id: int | None = None,
        sent_messages: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_proactive_event(event_id)
        if not current:
            raise ValueError(f"Proactive event not found: {event_id}")
        self.execute(
            """
            UPDATE proactive_events
            SET status = ?, action_type = ?, reason = ?, decision = ?, task_id = ?,
                sent_messages = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status or current["status"],
                action_type or current["action_type"],
                reason if reason is not None else current.get("reason"),
                self.to_json(decision) if decision is not None else self.to_json(current.get("decision")),
                task_id if task_id is not None else current.get("task_id"),
                self.to_json(sent_messages) if sent_messages is not None else self.to_json(current.get("sent_messages", [])),
                self.to_json(metadata) if metadata is not None else self.to_json(current.get("metadata", {})),
                self.now(),
                event_id,
            ),
        )
        return self.get_proactive_event(event_id)

    def _decode_proactive_event_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        row["decision"] = self.from_json(row.get("decision"), default=None)
        row["sent_messages"] = self.from_json(row.get("sent_messages"), default=[])
        row["metadata"] = self.from_json(row.get("metadata"), default={})
        return row

    def get_proactive_event(self, event_id: int) -> dict[str, Any] | None:
        row = self.fetch_one("SELECT * FROM proactive_events WHERE id = ?", (event_id,))
        return self._decode_proactive_event_row(row)

    def list_proactive_events(
        self,
        event_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.fetch_all(
            f"""
            SELECT * FROM proactive_events
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [self._decode_proactive_event_row(row) for row in rows]

    def set_proactive_setting(self, key: str, value: Any) -> None:
        now = self.now()
        self.execute(
            """
            INSERT INTO proactive_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, self.to_json(value), now),
        )

    def get_proactive_setting(self, key: str, default: Any = None) -> Any:
        row = self.fetch_one("SELECT value FROM proactive_settings WHERE key = ?", (key,))
        if not row:
            return default
        return self.from_json(row.get("value"), default=default)

    def get_latest_message_at(self, role: str | None = None) -> str | None:
        if role:
            row = self.fetch_one(
                "SELECT created_at FROM messages WHERE role = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (role,),
            )
        else:
            row = self.fetch_one("SELECT created_at FROM messages ORDER BY created_at DESC, id DESC LIMIT 1")
        return row.get("created_at") if row else None

    def count_proactive_events_since(
        self,
        start_at: str,
        status: str | None = None,
        action_type: str | None = None,
        task_name: str | None = None,
    ) -> int:
        clauses = ["created_at >= ?"]
        params: list[Any] = [start_at]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if action_type:
            clauses.append("action_type = ?")
            params.append(action_type)
        if task_name:
            clauses.append("json_extract(decision, '$.task.name') = ?")
            params.append(task_name)
        row = self.fetch_one(
            f"SELECT COUNT(*) AS count FROM proactive_events WHERE {' AND '.join(clauses)}",
            params,
        )
        return int(row.get("count", 0)) if row else 0

    def get_latest_proactive_event_at(self, status: str | None = None) -> str | None:
        if status:
            row = self.fetch_one(
                "SELECT updated_at FROM proactive_events WHERE status = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
                (status,),
            )
        else:
            row = self.fetch_one(
                "SELECT updated_at FROM proactive_events ORDER BY updated_at DESC, id DESC LIMIT 1"
            )
        return row.get("updated_at") if row else None
