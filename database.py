"""
本地数据库 —— 播放历史 / 收藏 / 搜索历史 / 直播收藏 / 配置管理 / 下载管理
使用 SQLite, 数据库文件存储在用户目录
"""

import os
import json
import time
import sqlite3
import threading
from typing import List, Dict, Optional


class Database:
    """SQLite 本地存储"""

    def __init__(self):
        self._local = threading.local()
        self.db_path = self._get_db_path()
        self._init_db()

    def _get_db_path(self) -> str:
        """获取数据库存储路径"""
        if os.name == 'nt':
            base = os.environ.get('APPDATA', os.path.expanduser('~'))
            app_dir = os.path.join(base, 'TVBoxDesktop')
        else:
            app_dir = os.path.expanduser('~/.tvbox-desktop')
        os.makedirs(app_dir, exist_ok=True)
        return os.path.join(app_dir, 'tvbox.db')

    def _get_conn(self) -> sqlite3.Connection:
        """每个线程独立连接"""
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn

    def _init_db(self):
        """初始化数据库表"""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vod_id TEXT NOT NULL,
                vod_name TEXT NOT NULL,
                vod_pic TEXT DEFAULT '',
                site_key TEXT NOT NULL,
                site_name TEXT NOT NULL,
                episode_index INTEGER DEFAULT 0,
                episode_name TEXT DEFAULT '',
                play_url TEXT DEFAULT '',
                position INTEGER DEFAULT 0,
                duration INTEGER DEFAULT 0,
                line_index INTEGER DEFAULT 0,
                updated_at INTEGER DEFAULT 0,
                UNIQUE(vod_id, site_key)
            );

            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vod_id TEXT NOT NULL,
                vod_name TEXT NOT NULL,
                vod_pic TEXT DEFAULT '',
                site_key TEXT NOT NULL,
                site_name TEXT NOT NULL,
                vod_remarks TEXT DEFAULT '',
                created_at INTEGER DEFAULT 0,
                UNIQUE(vod_id, site_key)
            );

            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL UNIQUE,
                search_count INTEGER DEFAULT 1,
                updated_at INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS live_favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_name TEXT NOT NULL,
                channel_url TEXT NOT NULL,
                group_name TEXT DEFAULT '',
                logo TEXT DEFAULT '',
                created_at INTEGER DEFAULT 0,
                UNIQUE(channel_name, channel_url)
            );

            CREATE TABLE IF NOT EXISTS live_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_name TEXT NOT NULL,
                channel_url TEXT NOT NULL,
                group_name TEXT DEFAULT '',
                updated_at INTEGER DEFAULT 0,
                UNIQUE(channel_name, channel_url)
            );

            CREATE TABLE IF NOT EXISTS configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                is_active INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT 0,
                UNIQUE(url)
            );

            CREATE TABLE IF NOT EXISTS live_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                source_type INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT 0,
                UNIQUE(url)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vod_name TEXT NOT NULL,
                episode_name TEXT DEFAULT '',
                url TEXT NOT NULL,
                file_path TEXT DEFAULT '',
                file_size INTEGER DEFAULT 0,
                downloaded INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at INTEGER DEFAULT 0,
                updated_at INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_history_updated ON history(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_search_count ON search_history(search_count DESC);
        """)
        conn.commit()

    # ======== 播放历史 ========

    def add_history(self, vod_id: str, vod_name: str, vod_pic: str,
                    site_key: str, site_name: str,
                    episode_index: int = 0, episode_name: str = "",
                    play_url: str = "", position: int = 0, duration: int = 0,
                    line_index: int = 0):
        conn = self._get_conn()
        now = int(time.time())
        conn.execute("""
            INSERT INTO history (vod_id, vod_name, vod_pic, site_key, site_name,
                                episode_index, episode_name, play_url, position,
                                duration, line_index, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(vod_id, site_key) DO UPDATE SET
                vod_name=excluded.vod_name,
                vod_pic=excluded.vod_pic,
                episode_index=excluded.episode_index,
                episode_name=excluded.episode_name,
                play_url=excluded.play_url,
                position=excluded.position,
                duration=excluded.duration,
                line_index=excluded.line_index,
                updated_at=excluded.updated_at
        """, (vod_id, vod_name, vod_pic, site_key, site_name,
              episode_index, episode_name, play_url, position, duration,
              line_index, now))
        conn.commit()

    def update_history_position(self, vod_id: str, site_key: str, position: int, duration: int = 0):
        conn = self._get_conn()
        now = int(time.time())
        conn.execute("""
            UPDATE history SET position=?, duration=?, updated_at=?
            WHERE vod_id=? AND site_key=?
        """, (position, duration, now, vod_id, site_key))
        conn.commit()

    def get_history(self, limit: int = 60) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT * FROM history ORDER BY updated_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_history_item(self, vod_id: str, site_key: str) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT * FROM history WHERE vod_id=? AND site_key=?
        """, (vod_id, site_key)).fetchone()
        return dict(row) if row else None

    def delete_history(self, vod_id: str = None, site_key: str = None, all_records: bool = False):
        conn = self._get_conn()
        if all_records:
            conn.execute("DELETE FROM history")
        elif vod_id and site_key:
            conn.execute("DELETE FROM history WHERE vod_id=? AND site_key=?", (vod_id, site_key))
        conn.commit()

    # ======== 收藏 ========

    def add_favorite(self, vod_id: str, vod_name: str, vod_pic: str,
                     site_key: str, site_name: str, vod_remarks: str = ""):
        conn = self._get_conn()
        now = int(time.time())
        try:
            conn.execute("""
                INSERT INTO favorites (vod_id, vod_name, vod_pic, site_key, site_name, vod_remarks, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (vod_id, vod_name, vod_pic, site_key, site_name, vod_remarks, now))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_favorite(self, vod_id: str, site_key: str):
        conn = self._get_conn()
        conn.execute("DELETE FROM favorites WHERE vod_id=? AND site_key=?", (vod_id, site_key))
        conn.commit()

    def is_favorite(self, vod_id: str, site_key: str) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT 1 FROM favorites WHERE vod_id=? AND site_key=?",
                           (vod_id, site_key)).fetchone()
        return row is not None

    def get_favorites(self) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM favorites ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    # ======== 搜索历史 ========

    def add_search_history(self, keyword: str):
        if not keyword or not keyword.strip():
            return
        keyword = keyword.strip()
        conn = self._get_conn()
        now = int(time.time())
        conn.execute("""
            INSERT INTO search_history (keyword, search_count, updated_at)
            VALUES (?, 1, ?)
            ON CONFLICT(keyword) DO UPDATE SET
                search_count = search_count + 1,
                updated_at = excluded.updated_at
        """, (keyword, now))
        conn.commit()

    def get_search_history(self, limit: int = 20) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT keyword, search_count, updated_at
            FROM search_history ORDER BY updated_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_search_suggestions(self, prefix: str, limit: int = 10) -> List[str]:
        """根据前缀获取搜索建议"""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT keyword FROM search_history
            WHERE keyword LIKE ? ORDER BY search_count DESC LIMIT ?
        """, (prefix + '%', limit)).fetchall()
        return [r['keyword'] for r in rows]

    def clear_search_history(self):
        conn = self._get_conn()
        conn.execute("DELETE FROM search_history")
        conn.commit()

    def remove_search_history(self, keyword: str):
        conn = self._get_conn()
        conn.execute("DELETE FROM search_history WHERE keyword=?", (keyword,))
        conn.commit()

    # ======== 直播收藏 ========

    def add_live_favorite(self, channel_name: str, channel_url: str,
                          group_name: str = "", logo: str = ""):
        conn = self._get_conn()
        now = int(time.time())
        try:
            conn.execute("""
                INSERT INTO live_favorites (channel_name, channel_url, group_name, logo, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (channel_name, channel_url, group_name, logo, now))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_live_favorite(self, channel_name: str, channel_url: str):
        conn = self._get_conn()
        conn.execute("DELETE FROM live_favorites WHERE channel_name=? AND channel_url=?",
                     (channel_name, channel_url))
        conn.commit()

    def is_live_favorite(self, channel_name: str, channel_url: str) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT 1 FROM live_favorites WHERE channel_name=? AND channel_url=?",
                           (channel_name, channel_url)).fetchone()
        return row is not None

    def get_live_favorites(self) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM live_favorites ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    # ======== 直播历史 ========

    def add_live_history(self, channel_name: str, channel_url: str, group_name: str = ""):
        conn = self._get_conn()
        now = int(time.time())
        conn.execute("""
            INSERT INTO live_history (channel_name, channel_url, group_name, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(channel_name, channel_url) DO UPDATE SET
                group_name=excluded.group_name,
                updated_at=excluded.updated_at
        """, (channel_name, channel_url, group_name, now))
        conn.commit()

    def get_live_history(self, limit: int = 30) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT * FROM live_history ORDER BY updated_at DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ======== 配置管理 ========

    def add_config(self, name: str, url: str) -> bool:
        conn = self._get_conn()
        now = int(time.time())
        try:
            conn.execute("""
                INSERT INTO configs (name, url, is_active, created_at)
                VALUES (?, ?, 0, ?)
            """, (name, url, now))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_config(self, config_id: int):
        conn = self._get_conn()
        conn.execute("DELETE FROM configs WHERE id=?", (config_id,))
        conn.commit()

    def set_active_config(self, config_id: int):
        conn = self._get_conn()
        conn.execute("UPDATE configs SET is_active=0")
        conn.execute("UPDATE configs SET is_active=1 WHERE id=?", (config_id,))
        conn.commit()

    def get_configs(self) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM configs ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_active_config(self) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM configs WHERE is_active=1").fetchone()
        return dict(row) if row else None

    # ======== 直播源管理 ========

    def add_live_config(self, name: str, url: str, source_type: int = 0) -> bool:
        conn = self._get_conn()
        now = int(time.time())
        try:
            conn.execute("""
                INSERT INTO live_configs (name, url, source_type, is_active, created_at)
                VALUES (?, ?, ?, 0, ?)
            """, (name, url, source_type, now))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_live_config(self, config_id: int):
        conn = self._get_conn()
        conn.execute("DELETE FROM live_configs WHERE id=?", (config_id,))
        conn.commit()

    def set_active_live_config(self, config_id: int):
        conn = self._get_conn()
        conn.execute("UPDATE live_configs SET is_active=0")
        conn.execute("UPDATE live_configs SET is_active=1 WHERE id=?", (config_id,))
        conn.commit()

    def get_live_configs(self) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM live_configs ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_active_live_config(self) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM live_configs WHERE is_active=1").fetchone()
        return dict(row) if row else None

    # ======== 下载管理 ========

    def add_download(self, vod_name: str, episode_name: str, url: str, file_path: str = "") -> int:
        conn = self._get_conn()
        now = int(time.time())
        cur = conn.execute("""
            INSERT INTO downloads (vod_name, episode_name, url, file_path, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """, (vod_name, episode_name, url, file_path, now, now))
        conn.commit()
        return cur.lastrowid

    def update_download_status(self, download_id: int, status: str,
                               downloaded: int = 0, file_size: int = 0):
        conn = self._get_conn()
        now = int(time.time())
        conn.execute("""
            UPDATE downloads SET status=?, downloaded=?, file_size=?, updated_at=?
            WHERE id=?
        """, (status, downloaded, file_size, now, download_id))
        conn.commit()

    def get_downloads(self) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM downloads ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def remove_download(self, download_id: int):
        conn = self._get_conn()
        conn.execute("DELETE FROM downloads WHERE id=?", (download_id,))
        conn.commit()

    # ======== 设置 ========

    def get_setting(self, key: str, default: str = "") -> str:
        conn = self._get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default

    def set_setting(self, key: str, value: str):
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, value))
        conn.commit()

    def get_all_settings(self) -> dict:
        conn = self._get_conn()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r['key']: r['value'] for r in rows}
