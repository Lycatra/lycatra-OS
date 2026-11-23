"""Private, per-device cache shared by the phone panel's workers."""
import json
import os
from pathlib import Path
import sqlite3

PIXEL = os.environ.get("LYCATRA_PHONE_ID", "")


class PhoneCache:
    def __init__(self, device):
        self.device = device
        os.umask(0o077)
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "omarchy-connect"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / "phone-cache.sqlite3"
        self.db = sqlite3.connect(path, timeout=4)
        path.chmod(0o600)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS phonebook (
                device TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS threads (
                device TEXT, thread TEXT, date INTEGER, payload TEXT NOT NULL,
                PRIMARY KEY (device, thread)
            );
            CREATE TABLE IF NOT EXISTS messages (
                device TEXT, thread TEXT, message TEXT, date INTEGER, payload TEXT NOT NULL,
                PRIMARY KEY (device, thread, message)
            );
            CREATE INDEX IF NOT EXISTS message_order
            ON messages (device, thread, date DESC);
        """)

    def phonebook(self):
        row = self.db.execute("SELECT payload FROM phonebook WHERE device = ?", (self.device,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_phonebook(self, data):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO phonebook VALUES (?, ?)", (self.device, json.dumps(data)))

    def threads(self):
        rows = self.db.execute(
            "SELECT payload FROM threads WHERE device = ? ORDER BY date DESC LIMIT 2000", (self.device,),
        )
        return [json.loads(payload) for payload, in rows]

    def history(self, thread, start, limit=35):
        rows = self.db.execute(
            """SELECT payload FROM messages WHERE device = ? AND thread = ?
               ORDER BY date DESC, message DESC LIMIT ? OFFSET ?""", (self.device, thread, limit, start),
        )
        return [json.loads(payload) for payload, in rows]

    def save_messages(self, messages):
        records = [(self.device, item["thread"], item["id"], item["date"], json.dumps(item)) for item in messages]
        if not records:
            return
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO messages VALUES (?, ?, ?, ?, ?)", records)
            self.db.executemany(
                """INSERT INTO threads VALUES (?, ?, ?, ?)
                   ON CONFLICT(device, thread) DO UPDATE SET date=excluded.date, payload=excluded.payload
                   WHERE excluded.date >= threads.date""",
                [(device, thread, date, payload) for device, thread, _, date, payload in records],
            )

    def remove_thread(self, thread):
        with self.db:
            self.db.execute("DELETE FROM threads WHERE device = ? AND thread = ?", (self.device, thread))
            self.db.execute("DELETE FROM messages WHERE device = ? AND thread = ?", (self.device, thread))

    def threads_to_warm(self):
        rows = self.db.execute(
            """SELECT t.thread FROM threads t WHERE t.device = ?
               AND (SELECT COUNT(*) FROM messages m WHERE m.device = t.device AND m.thread = t.thread) = 1
               ORDER BY t.date DESC LIMIT 2000""", (self.device,),
        )
        return [thread for thread, in rows]
