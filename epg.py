"""
EPG 节目单解析 —— XMLTV 格式
支持 .xml 和 .xml.gz, 自动刷新缓存
"""

import os
import gzip
import time
import threading
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional
from dataclasses import dataclass

import requests


@dataclass
class Programme:
    """节目"""
    start: str = ""
    stop: str = ""
    title: str = ""
    desc: str = ""
    channel_id: str = ""


class EpgParser:
    """XMLTV EPG 解析器"""

    def __init__(self):
        self._cache: Dict[str, List[Programme]] = {}  # channel_id -> programmes
        self._channel_names: Dict[str, str] = {}      # channel_id -> display_name
        self._load_time: int = 0
        self._url: str = ""
        self._lock = threading.Lock()
        self._refresh_thread: Optional[threading.Thread] = None

    def load(self, url: str) -> str:
        """加载 EPG (URL 或文件路径), 返回错误信息"""
        self._url = url
        try:
            if os.path.exists(url):
                with open(url, 'rb') as f:
                    data = f.read()
            else:
                resp = requests.get(url, timeout=30, headers={
                    "User-Agent": "Mozilla/5.0 (Linux; Android 12)"
                })
                data = resp.content

            # 解压 gzip
            if url.endswith('.gz') or data[:2] == b'\x1f\x8b':
                data = gzip.decompress(data)

            return self._parse(data)
        except Exception as e:
            return f"EPG 加载失败: {e}"

    def load_async(self, url: str):
        """异步加载 EPG"""
        self._refresh_thread = threading.Thread(
            target=self.load, args=(url,), daemon=True
        )
        self._refresh_thread.start()

    def _parse(self, data: bytes) -> str:
        """解析 XMLTV XML"""
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            return f"XML 解析失败: {e}"

        with self._lock:
            self._cache.clear()
            self._channel_names.clear()
            self._load_time = int(time.time())

            # 解析频道
            for ch in root.findall('.//channel'):
                ch_id = ch.get('id', '')
                name_el = ch.find('display-name')
                name = name_el.text if name_el is not None else ch_id
                self._channel_names[ch_id] = name

            # 解析节目表
            for prog in root.findall('.//programme'):
                ch_id = prog.get('channel', '')
                if not ch_id:
                    continue

                p = Programme(
                    start=prog.get('start', ''),
                    stop=prog.get('stop', ''),
                    channel_id=ch_id,
                )
                title_el = prog.find('title')
                if title_el is not None:
                    p.title = title_el.text or ''
                desc_el = prog.find('desc')
                if desc_el is not None:
                    p.desc = desc_el.text or ''

                if ch_id not in self._cache:
                    self._cache[ch_id] = []
                self._cache[ch_id].append(p)

            # 按时间排序
            for ch_id in self._cache:
                self._cache[ch_id].sort(key=lambda p: p.start)

        return ""

    def get_programmes(self, channel_id: str) -> List[dict]:
        """获取指定频道的节目列表"""
        with self._lock:
            progs = self._cache.get(channel_id, [])
            return [{"start": p.start, "stop": p.stop, "title": p.title, "desc": p.desc}
                    for p in progs]

    def get_current_programme(self, channel_id: str) -> Optional[dict]:
        """获取当前正在播放的节目"""
        now = time.strftime("%Y%m%d%H%M%S")
        with self._lock:
            progs = self._cache.get(channel_id, [])
            for p in progs:
                if p.start <= now <= (p.stop or '99999999999999'):
                    return {"start": p.start, "stop": p.stop, "title": p.title, "desc": p.desc}
        return None

    def get_channel_name(self, channel_id: str) -> str:
        return self._channel_names.get(channel_id, channel_id)

    def match_channel(self, channel_name: str) -> Optional[str]:
        """通过频道名模糊匹配 EPG channel_id"""
        with self._lock:
            for ch_id, name in self._channel_names.items():
                if channel_name.lower() in name.lower() or name.lower() in channel_name.lower():
                    return ch_id
        return None

    def to_dict(self) -> dict:
        """摘要信息"""
        with self._lock:
            return {
                "channel_count": len(self._channel_names),
                "programme_count": sum(len(v) for v in self._cache.values()),
                "load_time": self._load_time,
                "url": self._url,
            }
