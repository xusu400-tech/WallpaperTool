import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path.home() / ".WallpaperTool" / "history.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

THUMB_DIR = Path.home() / ".WallpaperTool" / "thumbs"
THUMB_DIR.mkdir(parents=True, exist_ok=True)


class HistoryDB:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self._init_tables()
        self._migrate()

    def _init_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                source_url TEXT,
                tags TEXT,
                resolution TEXT,
                style TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                file_name TEXT NOT NULL,
                source_url TEXT,
                thumb_path TEXT,
                resolution TEXT,
                source TEXT DEFAULT 'wallhaven',
                created_at TEXT NOT NULL
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_time ON history(timestamp DESC)")
        self.conn.commit()

    def _migrate(self):
        """旧库自动补列（source / thumb_path）"""
        cur = self.conn.execute("PRAGMA table_info(history)")
        cols = {row[1] for row in cur.fetchall()}
        if "source" not in cols:
            self.conn.execute("ALTER TABLE history ADD COLUMN source TEXT DEFAULT 'wallhaven'")
        if "thumb_path" not in cols:
            self.conn.execute("ALTER TABLE history ADD COLUMN thumb_path TEXT")
        self.conn.commit()

    # ---------- 历史 ----------
    def add_record(self, file_name, file_path, source_url="",
                   tags="", resolution="", style="fill",
                   source="wallhaven", thumb_path=""):
        self.conn.execute(
            """INSERT INTO history (timestamp, file_name, file_path, source_url,
               tags, resolution, style, source, thumb_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now().isoformat(), file_name, file_path,
             source_url, tags, resolution, style, source, thumb_path)
        )
        self.conn.commit()

    def get_all(self, limit=100):
        cur = self.conn.execute(
            """SELECT id, timestamp, file_name, file_path, source_url,
                      tags, resolution, style, source, thumb_path
               FROM history ORDER BY timestamp DESC LIMIT ?""",
            (limit,))
        return cur.fetchall()

    def clear_all(self):
        self.conn.execute("DELETE FROM history")
        self.conn.commit()

    # ---------- 收藏 ----------
    def add_favorite(self, file_path, file_name, source_url="",
                     thumb_path="", resolution="", source="wallhaven"):
        try:
            self.conn.execute(
                """INSERT INTO favorites (file_path, file_name, source_url,
                   thumb_path, resolution, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (file_path, file_name, source_url, thumb_path,
                 resolution, source, datetime.now().isoformat())
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_favorite(self, key):
        """按 file_path 或 source_url 取消收藏"""
        self.conn.execute(
            "DELETE FROM favorites WHERE file_path=? OR source_url=?",
            (key, key))
        self.conn.commit()

    def is_favorite(self, key):
        cur = self.conn.execute(
            "SELECT 1 FROM favorites WHERE file_path=? OR source_url=?",
            (key, key))
        return cur.fetchone() is not None

    def get_favorites(self, limit=500):
        cur = self.conn.execute(
            """SELECT id, file_path, file_name, source_url, thumb_path,
                      resolution, source, created_at
               FROM favorites ORDER BY created_at DESC LIMIT ?""",
            (limit,))
        return cur.fetchall()

    def close(self):
        self.conn.close()
        
    def delete_record(self, rid):
        self.conn.execute("DELETE FROM history WHERE id=?", (rid,))
        self.conn.commit()

    def delete_favorite_by_id(self, fid):
        self.conn.execute("DELETE FROM favorites WHERE id=?", (fid,))
        self.conn.commit()

