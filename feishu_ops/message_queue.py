"""消息队列 + 超时检测"""
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from threading import Lock

@dataclass
class PendingMessage:
    message_id: str
    user_id: str
    chat_id: str
    content: str
    timestamp: float
    replied: bool = False
    reply_content: Optional[str] = None
    short_id: int = 0  # 人类友好的短序号，owner 指令使用

class MessageQueue:
    def __init__(self, timeout_seconds: int = 600, db_path: Optional[str] = None):
        self.timeout_seconds = timeout_seconds
        self.db_path = db_path
        self._queue: Dict[str, PendingMessage] = {}
        self._lock = Lock()
        self._next_short_id = 1
        if self.db_path:
            self._init_db()
            self._load_pending_messages()

    def add(self, message_id: str, user_id: str, chat_id: str, content: str) -> PendingMessage:
        with self._lock:
            msg = PendingMessage(
                message_id=message_id,
                user_id=user_id,
                chat_id=chat_id,
                content=content,
                timestamp=time.time(),
                short_id=self._next_short_id,
            )
            self._next_short_id += 1
            self._queue[message_id] = msg
            self._sync_message(msg)
            return msg

    def get_by_short_id(self, short_id: int) -> Optional[PendingMessage]:
        with self._lock:
            for msg in self._queue.values():
                if msg.short_id == short_id and not msg.replied:
                    return msg
            return None

    def force_timeout(self, short_id: int) -> Optional[PendingMessage]:
        """将指定消息的 timestamp 推远，使下一轮 check 立即触发自动回复"""
        with self._lock:
            for msg in self._queue.values():
                if msg.short_id == short_id and not msg.replied:
                    msg.timestamp = 0
                    self._sync_message(msg)
                    return msg
            return None

    def mark_replied(self, message_id: str, reply_content: str) -> None:
        with self._lock:
            if message_id in self._queue:
                self._queue[message_id].replied = True
                self._queue[message_id].reply_content = reply_content
                self._sync_message(self._queue[message_id])

    def get_timeout_messages(self) -> List[PendingMessage]:
        """获取超时的消息"""
        current_time = time.time()
        timeout_messages = []
        with self._lock:
            for msg in self._queue.values():
                if not msg.replied and (current_time - msg.timestamp) >= self.timeout_seconds:
                    timeout_messages.append(msg)
        return timeout_messages

    def remove(self, message_id: str) -> None:
        with self._lock:
            if message_id in self._queue:
                del self._queue[message_id]
            self._delete_message(message_id)

    def cancel_by_chat(self, chat_id: str, reply_content: str = "") -> int:
        cancelled = 0
        with self._lock:
            message_ids = [
                message_id
                for message_id, msg in self._queue.items()
                if msg.chat_id == chat_id and not msg.replied
            ]
            for message_id in message_ids:
                self._queue[message_id].replied = True
                self._queue[message_id].reply_content = reply_content
                del self._queue[message_id]
                self._delete_message(message_id)
                cancelled += 1
        return cancelled

    def size(self) -> int:
        with self._lock:
            return len(self._queue)

    def _init_db(self) -> None:
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_messages (
                    message_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    replied INTEGER NOT NULL DEFAULT 0,
                    reply_content TEXT,
                    short_id INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            cursor.close()
            conn.commit()
        finally:
            conn.close()
        # 老 DB 升级：增量加 short_id 列、忽略已存在异常
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("ALTER TABLE pending_messages ADD COLUMN short_id INTEGER NOT NULL DEFAULT 0")
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _load_pending_messages(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """
                SELECT message_id, user_id, chat_id, content, timestamp, replied, reply_content, short_id
                FROM pending_messages
                WHERE replied = 0
                ORDER BY timestamp
                """
            )
            rows = cursor.fetchall()
            cursor.close()
        finally:
            conn.close()
        max_short_id = 0
        for row in rows:
            short_id = int(row[7]) if row[7] else 0
            self._queue[row[0]] = PendingMessage(
                message_id=row[0],
                user_id=row[1],
                chat_id=row[2],
                content=row[3],
                timestamp=float(row[4]),
                replied=bool(row[5]),
                reply_content=row[6],
                short_id=short_id,
            )
            if short_id > max_short_id:
                max_short_id = short_id
        self._next_short_id = max_short_id + 1

    def _sync_message(self, msg: PendingMessage) -> None:
        if not self.db_path:
            return
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO pending_messages
                (message_id, user_id, chat_id, content, timestamp, replied, reply_content, short_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.message_id,
                    msg.user_id,
                    msg.chat_id,
                    msg.content,
                    msg.timestamp,
                    1 if msg.replied else 0,
                    msg.reply_content,
                    msg.short_id,
                ),
            )
            cursor.close()
            conn.commit()
        finally:
            conn.close()

    def _delete_message(self, message_id: str) -> None:
        if not self.db_path:
            return
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("DELETE FROM pending_messages WHERE message_id = ?", (message_id,))
            cursor.close()
            conn.commit()
        finally:
            conn.close()