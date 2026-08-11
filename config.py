"""
TVBox 配置解析器 —— 数据模型与配置加载
兼容 TVBoxOSC / FongMi TV 的 JSON 配置格式
"""

import json
import os
import requests
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class Site:
    """点播站点"""
    key: str
    name: str
    type: int = 0          # 0=JSON API, 1=JAR, 3=Python, 4=JS
    api: str = ""
    searchable: int = 1
    quickSearch: int = 1
    filterable: int = 0
    ext: str = ""
    jar: str = ""
    categories: List[str] = field(default_factory=list)
    player_url: str = ""


@dataclass
class LiveSource:
    """直播源"""
    name: str
    type: int = 0           # 0=M3U, 1=TXT(#genre#), 2=JSON
    url: str = ""
    epg: str = ""
    logo: str = ""


@dataclass
class Parse:
    """解析器"""
    name: str
    type: int = 0
    url: str = ""
    ext: dict = field(default_factory=dict)


@dataclass
class VodItem:
    """视频条目"""
    vod_id: str = ""
    vod_name: str = ""
    vod_pic: str = ""
    vod_remarks: str = ""
    type_id: str = ""
    type_name: str = ""
    vod_year: str = ""
    vod_area: str = ""
    vod_actor: str = ""
    vod_director: str = ""
    vod_content: str = ""
    vod_play_from: str = ""
    vod_play_url: str = ""
    vod_score: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class Category:
    """分类"""
    type_id: str
    type_name: str


@dataclass
class FilterGroup:
    """筛选组"""
    key: str
    name: str
    values: List[Dict[str, str]] = field(default_factory=list)


class Config:
    """TVBox 配置管理"""

    def __init__(self):
        self.sites: List[Site] = []
        self.lives: List[LiveSource] = []
        self.parses: List[Parse] = []
        self.doh: List[dict] = []
        self.proxy: str = ""
        self.hosts: List[dict] = []
        self.raw: dict = {}
        self._site_map: Dict[str, Site] = {}

    def load_from_url(self, url: str) -> str:
        """从 URL 加载配置, 返回错误信息(空字符串表示成功)"""
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"}
            resp = requests.get(url, headers=headers, timeout=15)
            resp.encoding = resp.apparent_encoding
            return self._parse(resp.text)
        except Exception as e:
            return f"加载配置失败: {e}"

    def load_from_file(self, path: str) -> str:
        """从本地文件加载配置"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return self._parse(f.read())
        except Exception as e:
            return f"加载文件失败: {e}"

    def _parse(self, text: str) -> str:
        """解析 JSON 文本"""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试去掉 BOM 和注释
            text = text.strip().lstrip('\ufeff')
            try:
                data = json.loads(text)
            except Exception as e:
                return f"JSON 解析失败: {e}"

        self.raw = data
        self.sites.clear()
        self.lives.clear()
        self.parses.clear()
        self._site_map.clear()

        # 解析站点
        for s in data.get("sites", []):
            site = Site(
                key=s.get("key", ""),
                name=s.get("name", ""),
                type=s.get("type", 0),
                api=s.get("api", ""),
                searchable=s.get("searchable", 1),
                quickSearch=s.get("quickSearch", 1),
                filterable=s.get("filterable", 0),
                ext=s.get("ext", ""),
                jar=s.get("jar", ""),
                categories=s.get("categories", []),
                player_url=s.get("playerUrl", ""),
            )
            self.sites.append(site)
            self._site_map[site.key] = site

        # 解析直播源
        for l in data.get("lives", []):
            self.lives.append(LiveSource(
                name=l.get("name", ""),
                type=l.get("type", 0),
                url=l.get("url", ""),
                epg=l.get("epg", ""),
                logo=l.get("logo", ""),
            ))

        # 解析解析器
        for p in data.get("parses", []):
            self.parses.append(Parse(
                name=p.get("name", ""),
                type=p.get("type", 0),
                url=p.get("url", ""),
                ext=p.get("ext", {}),
            ))

        # 网络设置
        self.doh = data.get("doh", [])
        self.proxy = data.get("proxy", "")
        self.hosts = data.get("hosts", [])

        return ""

    def get_site(self, key: str) -> Optional[Site]:
        return self._site_map.get(key)

    def get_searchable_sites(self) -> List[Site]:
        return [s for s in self.sites if s.searchable == 1]

    def to_summary(self) -> dict:
        return {
            "site_count": len(self.sites),
            "live_count": len(self.lives),
            "parse_count": len(self.parses),
            "sites": [{"key": s.key, "name": s.name, "type": s.type,
                        "searchable": s.searchable} for s in self.sites],
            "lives": [{"name": l.name, "url": l.url} for l in self.lives],
        }
