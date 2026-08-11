"""
直播源解析器 —— 支持 M3U / TXT(#genre#) / JSON 三种格式
"""

import json
import re
import requests
from typing import List, Dict
from dataclasses import dataclass, field


@dataclass
class Channel:
    """频道"""
    name: str
    url: str
    logo: str = ""
    group: str = "未分类"
    tvg_id: str = ""
    tvg_name: str = ""


class LiveParser:
    """直播源解析"""

    def __init__(self):
        self.groups: Dict[str, List[Channel]] = {}
        self.channels: List[Channel] = []
        self.epg_url: str = ""

    def parse(self, url: str, source_type: int = 0, epg: str = "") -> str:
        """解析直播源, 返回错误信息(空表示成功)"""
        self.epg_url = epg
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"}
            resp = requests.get(url, headers=headers, timeout=15)
            resp.encoding = resp.apparent_encoding
            text = resp.text
        except Exception as e:
            return f"获取直播源失败: {e}"

        if source_type == 0:
            return self._parse_m3u(text)
        elif source_type == 1:
            return self._parse_txt(text)
        elif source_type == 2:
            return self._parse_json(text)
        else:
            # 自动检测
            text_stripped = text.strip()
            if text_stripped.startswith("#EXTM3U"):
                return self._parse_m3u(text)
            elif text_stripped.startswith("{") or text_stripped.startswith("["):
                return self._parse_json(text)
            else:
                return self._parse_txt(text)

    def _parse_m3u(self, text: str) -> str:
        """解析 M3U 格式"""
        self.groups.clear()
        self.channels.clear()

        lines = text.strip().split("\n")
        current_channel = Channel(name="", url="")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith("#EXTINF"):
                # 解析 EXTINF 属性
                attrs = {}
                # 提取 tvg-id, tvg-name, tvg-logo, group-title
                for match in re.finditer(r'([\w-]+)="([^"]*)"', line):
                    attrs[match.group(1)] = match.group(2)

                # 频道名在逗号后面
                name_part = line.split(",", 1)
                name = name_part[1].strip() if len(name_part) > 1 else ""

                current_channel = Channel(
                    name=name,
                    url="",
                    logo=attrs.get("tvg-logo", ""),
                    group=attrs.get("group-title", "未分类"),
                    tvg_id=attrs.get("tvg-id", ""),
                    tvg_name=attrs.get("tvg-name", name),
                )
            elif line.startswith("#EXTM3U") or line.startswith("#"):
                continue
            else:
                # URL 行
                if current_channel.name:
                    current_channel.url = line
                    self.channels.append(current_channel)
                    grp = current_channel.group
                    if grp not in self.groups:
                        self.groups[grp] = []
                    self.groups[grp].append(current_channel)
                    current_channel = Channel(name="", url="")

        return ""

    def _parse_txt(self, text: str) -> str:
        """解析 TXT 格式 (#genre# 分组)"""
        self.groups.clear()
        self.channels.clear()

        current_group = "未分类"
        lines = text.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if "#genre#" in line.lower():
                # 分组行: "央视频道,#genre#"
                parts = line.split(",")
                current_group = parts[0].strip()
                if current_group not in self.groups:
                    self.groups[current_group] = []
                continue

            # 频道行: "CCTV-1,http://..."
            parts = line.split(",", 1)
            if len(parts) == 2:
                name = parts[0].strip()
                url = parts[1].strip()
                if url and name:
                    ch = Channel(name=name, url=url, group=current_group)
                    self.channels.append(ch)
                    if current_group not in self.groups:
                        self.groups[current_group] = []
                    self.groups[current_group].append(ch)

        return ""

    def _parse_json(self, text: str) -> str:
        """解析 JSON 格式"""
        self.groups.clear()
        self.channels.clear()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return f"JSON 解析失败: {e}"

        # 兼容多种 JSON 格式
        groups_data = data if isinstance(data, list) else data.get("groups", data.get("lives", []))

        for grp in groups_data:
            group_name = grp.get("name", grp.get("group", "未分类"))
            channels = grp.get("channels", grp.get("list", []))
            if isinstance(channels, dict):
                # {"频道名": "url"} 格式
                for name, url in channels.items():
                    ch = Channel(name=name, url=url, group=group_name)
                    self.channels.append(ch)
                    if group_name not in self.groups:
                        self.groups[group_name] = []
                    self.groups[group_name].append(ch)
            elif isinstance(channels, list):
                for ch_data in channels:
                    name = ch_data.get("name", ch_data.get("title", ""))
                    urls = ch_data.get("urls", ch_data.get("url", ""))
                    if isinstance(urls, str):
                        urls = [urls]
                    for u in urls:
                        ch = Channel(
                            name=name, url=u,
                            logo=ch_data.get("logo", ""),
                            group=group_name,
                        )
                        self.channels.append(ch)
                        if group_name not in self.groups:
                            self.groups[group_name] = []
                        self.groups[group_name].append(ch)

        return ""

    def to_dict(self) -> dict:
        """序列化为前端可用的结构"""
        return {
            "groups": {
                g: [{"name": c.name, "url": c.url, "logo": c.logo}
                     for c in channels]
                for g, channels in self.groups.items()
            },
            "group_names": list(self.groups.keys()),
            "channel_count": len(self.channels),
            "epg": self.epg_url,
        }
