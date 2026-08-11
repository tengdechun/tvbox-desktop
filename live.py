"""
直播源解析器 —— 完整实现 FongMi/TV 兼容的直播功能

支持格式:
  - M3U / M3U8 (含扩展属性)
  - TXT (#genre# 分组, 含密码保护)
  - JSON (TVBox lives 格式)

支持特性:
  - M3U 扩展属性: tvg-url, tvg-id, tvg-name, tvg-chno, tvg-logo, group-title,
    http-user-agent, catchup, catchup-source, catchup-replace
  - #EXTHTTP: JSON 请求头指令
  - #EXTVLCOPT: VLC 风格指令 (http-user-agent, http-referrer, http-origin)
  - 多线路备援 (URL 中以 # 分隔)
  - 行内标头 (URL 后 |key=value, 支持 & 连接)
  - 频道密码保护 (分组 名称_密码,#genre#)
  - Catchup 时移 (append / default, 支持 {(b)fmt}, {(e)fmt}, {utc}, {utcend})
  - 频道指令: ua, origin, referer, header, format, parse, click, forceKey
  - TVBus / ForceTech 引擎识别
  - 从 LiveSource 配置加载 (api, ext, ua, origin, referer, timeZone, boot, pass, groups)

Python 3.8+ 兼容
"""

import json
import re
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple, Any, Union
from dataclasses import dataclass, field

import requests


# ======================== 数据结构 ========================

@dataclass
class Channel:
    """频道"""
    name: str
    url: str
    logo: str = ""
    group: str = "未分类"
    tvg_id: str = ""
    tvg_name: str = ""
    tvg_chno: str = ""
    http_user_agent: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    catchup_type: str = ""
    catchup_source: str = ""
    catchup_regex: str = ""
    catchup_replace: str = ""
    parse: int = 0
    format: str = ""
    click: str = ""
    force_key: str = ""
    multi_urls: List[str] = field(default_factory=list)

    def all_urls(self) -> List[str]:
        """获取所有播放URL(主URL + 备用), 依序尝试"""
        urls = []
        if self.url:
            urls.append(self.url)
        for u in self.multi_urls:
            if u and u not in urls:
                urls.append(u)
        return urls

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "logo": self.logo,
            "group": self.group,
            "tvg_id": self.tvg_id,
            "tvg_name": self.tvg_name,
            "tvg_chno": self.tvg_chno,
            "http_user_agent": self.http_user_agent,
            "headers": dict(self.headers),
            "catchup_type": self.catchup_type,
            "catchup_source": self.catchup_source,
            "catchup_regex": self.catchup_regex,
            "catchup_replace": self.catchup_replace,
            "parse": self.parse,
            "format": self.format,
            "click": self.click,
            "force_key": self.force_key,
            "multi_urls": list(self.multi_urls),
            "multi_urls_count": len(self.multi_urls),
            "is_tvbus": url_is_tvbus(self.url),
            "is_forcetech": url_is_forcetech(self.url),
        }


@dataclass
class Group:
    """分组 (含密码保护)"""
    name: str = ""
    password: str = ""
    channels: List[Channel] = field(default_factory=list)

    @property
    def needs_password(self) -> bool:
        return bool(self.password)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "password": self.password,
            "needs_password": self.needs_password,
            "channel_count": len(self.channels),
            "channels": [c.to_dict() for c in self.channels],
        }


@dataclass
class LiveSourceConfig:
    """LiveSource 配置 (兼容 FongMi/TV)
    字段对应 TVBox JSON 中 lives[] 的条目
    """
    name: str = ""
    type: int = 0
    url: str = ""
    epg: str = ""
    logo: str = ""
    api: str = ""          # spider 类名 (type=3 时)
    ext: str = ""          # 扩展参数
    ua: str = ""           # User-Agent
    origin: str = ""
    referer: str = ""
    time_zone: str = ""
    boot: bool = False     # 启动自动选中
    skip_password: bool = False  # 跳过密码 (JSON key: pass)
    groups: List[dict] = field(default_factory=list)  # 内嵌频道分组

    @classmethod
    def from_dict(cls, d: dict) -> "LiveSourceConfig":
        if not isinstance(d, dict):
            return cls()
        return cls(
            name=d.get("name", ""),
            type=d.get("type", 0),
            url=d.get("url", ""),
            epg=d.get("epg", ""),
            logo=d.get("logo", ""),
            api=d.get("api", ""),
            ext=d.get("ext", ""),
            ua=d.get("ua", d.get("userAgent", "")),
            origin=d.get("origin", ""),
            referer=d.get("referer", d.get("ref", "")),
            time_zone=d.get("timeZone", d.get("timezone", "")),
            boot=bool(d.get("boot", False)),
            skip_password=bool(d.get("pass", False)),
            groups=d.get("groups", []) or [],
        )

    def to_headers(self) -> Dict[str, str]:
        headers = {}
        if self.ua:
            headers["User-Agent"] = self.ua
        if self.origin:
            headers["Origin"] = self.origin
        if self.referer:
            headers["Referer"] = self.referer
        return headers


# ======================== 工具函数 ========================

# 密码启发式: 字母数字/下划线/横线 (避免误把含中文/空格的部分当作密码)
_PWD_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

# M3U 属性正则: key="value"
_M3U_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def url_is_tvbus(url: str) -> bool:
    """判断是否为 TVBus 格式"""
    return bool(url) and url.lower().startswith("tvbus://")


def url_is_forcetech(url: str) -> bool:
    """判断是否为 ForceTech 格式"""
    if not url:
        return False
    lower = url.lower()
    return lower.startswith("forcetech://") or "forcetech" in lower


def parse_group_password(raw_name: str) -> Tuple[str, str]:
    """从分组名解析密码: 名称_密码 -> (名称, 密码)
    仅当 _ 后部分为字母数字时才视为密码"""
    if not raw_name:
        return raw_name, ""
    if "_" in raw_name:
        name, _sep, pwd = raw_name.rpartition("_")
        if pwd and _PWD_RE.match(pwd):
            return name, pwd
    return raw_name, ""


def _split_backup_urls(url_str: str) -> List[str]:
    """按 # 分隔多个备用URL
    若 # 后部分含 :// 则视为新URL, 否则视为fragment附加到前一个URL
    """
    if "#" not in url_str:
        return [url_str] if url_str else []
    parts = url_str.split("#")
    urls: List[str] = []
    current = parts[0]
    for part in parts[1:]:
        # 包含 :// 或以 // 开头 => 视为独立URL
        if "://" in part or part.startswith("//"):
            if current:
                urls.append(current)
            current = part
        else:
            # fragment, 附加回去
            current = current + "#" + part
    if current:
        urls.append(current)
    return urls


def _split_inline_params(param_str: str) -> List[str]:
    """拆分行内参数, 支持 & 和 | 分隔, 尊重 {} 内的 JSON"""
    parts: List[str] = []
    depth = 0
    current = ""
    for ch in param_str:
        if ch == "{":
            depth += 1
            current += ch
        elif ch == "}":
            depth = max(0, depth - 1)
            current += ch
        elif ch in "&|" and depth == 0:
            if current:
                parts.append(current)
            current = ""
        else:
            current += ch
    if current:
        parts.append(current)
    return parts


@dataclass
class _ParsedUrl:
    """行内URL解析结果 (内部使用)"""
    url: str = ""
    multi_urls: List[str] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    parse: int = 0
    format: str = ""
    click: str = ""
    force_key: str = ""
    http_user_agent: str = ""


def _parse_single_url(url_line: str) -> _ParsedUrl:
    """解析单个URL行 (不含#备援分隔), 提取 | 后的行内标头与指令"""
    result = _ParsedUrl()
    if not url_line:
        return result

    pipe_idx = url_line.find("|")
    if pipe_idx < 0:
        result.url = url_line.strip()
        return result

    result.url = url_line[:pipe_idx].strip()
    param_part = url_line[pipe_idx + 1:]

    for pair in _split_inline_params(param_part):
        if "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        key = key.strip().lower()
        value = value.strip()

        if key == "ua":
            result.http_user_agent = value
            result.headers["User-Agent"] = value
        elif key == "origin":
            result.headers["Origin"] = value
        elif key in ("referer", "ref"):
            result.headers["Referer"] = value
        elif key == "header":
            if value.startswith("{"):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, dict):
                        for hk, hv in parsed.items():
                            result.headers[hk] = str(hv)
                except json.JSONDecodeError:
                    pass
            else:
                # key:value,key2:value2 格式
                for h in value.split(","):
                    if ":" in h:
                        hk, _, hv = h.partition(":")
                        result.headers[hk.strip()] = hv.strip()
        elif key == "format":
            result.format = value
        elif key == "parse":
            try:
                result.parse = int(value)
            except ValueError:
                result.parse = 1 if value else 0
        elif key == "click":
            result.click = value
        elif key == "forcekey":
            result.force_key = value

    return result


def parse_inline_url(raw_url: str) -> _ParsedUrl:
    """解析行内URL, 提取主URL/备用URL/行内标头/指令
    1. 按 # 分隔备用URL
    2. 每条URL按 | 分离行内参数
    主URL的行内参数成为频道属性, 备用URL仅记录URL
    """
    if not raw_url:
        return _ParsedUrl()

    url_lines = _split_backup_urls(raw_url)
    if not url_lines:
        return _ParsedUrl()

    primary = _parse_single_url(url_lines[0])

    for backup_raw in url_lines[1:]:
        backup = _parse_single_url(backup_raw)
        if backup.url:
            primary.multi_urls.append(backup.url)

    return primary


# Catchup 时间变量正则 (不在 f-string 中, 避免反斜杠问题)
_CATCHUP_B_RE = re.compile(r"\{\(b\)([^}]*)\}")
_CATCHUP_E_RE = re.compile(r"\{\(e\)([^}]*)\}")
_CATCHUP_UTC_RE = re.compile(r"\{utc(?::([^}]*))?\}")
_CATCHUP_UTCEND_RE = re.compile(r"\{utcend(?::([^}]*))?\}")


# ======================== 解析器 ========================

class LiveParser:
    """直播源解析器"""

    def __init__(self):
        # 向后兼容: group_name -> [Channel]
        self.groups: Dict[str, List[Channel]] = {}
        # 结构化分组 (含密码)
        self.group_list: List[Group] = []
        # 所有频道扁平列表
        self.channels: List[Channel] = []

        # EPG / tvg-url
        self.epg_url: str = ""
        self.tvg_url: str = ""

        # 全局 catchup 默认值 (来自 #EXTM3U)
        self.global_catchup: str = ""
        self.global_catchup_source: str = ""
        self.global_catchup_replace: str = ""

        # LiveSource 配置
        self.source_config: Optional[LiveSourceConfig] = None
        self.time_zone: str = ""
        self.boot: bool = False
        self.skip_password: bool = False

    # ---------- 公开接口 ----------

    def parse(self, url: str, source_type: int = 0, epg: str = "") -> str:
        """解析直播源, 返回错误信息(空表示成功)

        source_type:
          0 = M3U
          1 = TXT (#genre#)
          2 = JSON
          3 = Spider/API (尝试从 url 获取)
          其他 = 自动检测
        """
        self.epg_url = epg
        self.time_zone = ""
        self.skip_password = False

        try:
            headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"}
            resp = requests.get(url, headers=headers, timeout=15)
            resp.encoding = resp.apparent_encoding
            text = resp.text
        except Exception as e:
            return f"获取直播源失败: {e}"

        fmt = self._resolve_format(text, source_type)
        if fmt == "m3u":
            return self._parse_m3u(text)
        elif fmt == "txt":
            return self._parse_txt(text)
        elif fmt == "json":
            return self._parse_json(text)
        return "未知直播源格式"

    def parse_source(self, source: Union[dict, LiveSourceConfig, Any]) -> str:
        """从 LiveSource 配置加载直播源

        source: LiveSourceConfig 实例, 或 dict, 或具有 name/type/url/epg/api/ext/
                ua/origin/referer/timeZone/boot/pass/groups 属性的对象
        """
        if isinstance(source, dict):
            source = LiveSourceConfig.from_dict(source)
        elif not isinstance(source, LiveSourceConfig):
            source = LiveSourceConfig.from_dict(_obj_to_dict(source))

        self.source_config = source
        self.epg_url = source.epg
        self.time_zone = source.time_zone
        self.boot = source.boot
        self.skip_password = source.skip_password

        source_headers = source.to_headers()

        # 1. 内嵌 groups (无需获取URL)
        if source.groups:
            self._clear()
            for grp in source.groups:
                if not isinstance(grp, dict):
                    continue
                raw_name = grp.get("name", grp.get("group", "未分类")) or "未分类"
                display_name, password = parse_group_password(raw_name)
                self._ensure_group(display_name, password)
                channels = grp.get("channels", grp.get("list", []))
                self._add_json_channels(channels, display_name, source_headers)
            return ""

        # 2. 从 URL 获取
        if not source.url:
            if source.type == 3 and source.api:
                return "Spider 类(type=3)直播源需通过 api 动态获取, 桌面端暂不支持"
            return "直播源 URL 为空"

        # 构建请求头
        req_headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"}
        req_headers.update(source_headers)

        try:
            resp = requests.get(source.url, headers=req_headers, timeout=15)
            resp.encoding = resp.apparent_encoding
            text = resp.text
        except Exception as e:
            return f"获取直播源失败: {e}"

        fmt = self._resolve_format(text, source.type)
        if fmt == "m3u":
            err = self._parse_m3u(text)
        elif fmt == "txt":
            err = self._parse_txt(text)
        elif fmt == "json":
            err = self._parse_json(text)
        else:
            return "未知直播源格式"

        if err:
            return err

        # 应用 source 级请求头到所有频道
        if source_headers:
            for ch in self.channels:
                self._merge_headers(ch, source_headers)

        # type=3 (spider): 记录 api 信息
        if source.type == 3 and source.api:
            for ch in self.channels:
                if not ch.force_key:
                    ch.force_key = source.api
                ch.parse = 3

        return ""

    # ---------- 格式判断 ----------

    def _resolve_format(self, text: str, source_type: int) -> str:
        """确定解析格式: m3u / txt / json"""
        # 显式指定
        if source_type == 0:
            return "m3u"
        if source_type == 1:
            return "txt"
        if source_type == 2:
            return "json"
        if source_type == 3:
            # spider: 尝试自动检测内容
            pass
        # 自动检测
        return self._auto_detect(text)

    def _auto_detect(self, text: str) -> str:
        """自动检测格式:
        JSON: 内容以 [ 或 { 开头
        M3U: 任一行含 #EXTM3U (且不含 #genre#)
        TXT: 其他
        """
        stripped = text.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                json.loads(stripped)
                return "json"
            except json.JSONDecodeError:
                pass
        if "#EXTM3U" in text and "#genre#" not in text.lower():
            return "m3u"
        return "txt"

    # ---------- M3U 解析 ----------

    def _parse_m3u(self, text: str) -> str:
        """解析 M3U 格式 (含扩展属性)"""
        self._clear()

        lines = text.splitlines()
        pending_headers: Dict[str, str] = {}
        current_channel: Optional[Channel] = None

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("#EXTM3U"):
                self._parse_extm3u_attrs(line)

            elif line.startswith("#EXTINF"):
                current_channel = self._parse_extinf(line)

            elif line.startswith("#EXTHTTP:"):
                json_str = line[len("#EXTHTTP:"):].strip()
                if json_str:
                    try:
                        parsed = json.loads(json_str)
                        if isinstance(parsed, dict):
                            pending_headers.update(
                                {str(k): str(v) for k, v in parsed.items()}
                            )
                    except json.JSONDecodeError:
                        pass

            elif line.startswith("#EXTVLCOPT:"):
                opt = line[len("#EXTVLCOPT:"):].strip()
                if "=" in opt:
                    key, _, value = opt.partition("=")
                    key = key.strip().lower()
                    value = value.strip()
                    if key == "http-user-agent":
                        pending_headers["User-Agent"] = value
                    elif key == "http-referrer":
                        pending_headers["Referer"] = value
                    elif key == "http-origin":
                        pending_headers["Origin"] = value

            elif line.startswith("#EXTGRP:"):
                # #EXTGRP:分组名 — 为当前频道设置分组 (group-title 的替代)
                if current_channel is not None:
                    grp = line[len("#EXTGRP:"):].strip()
                    if grp:
                        current_channel.group = grp

            elif line.startswith("#"):
                # 其他指令, 忽略
                continue

            else:
                # URL 行
                if current_channel is not None:
                    self._finalize_m3u_channel(current_channel, line, pending_headers)
                    current_channel = None
                    pending_headers = {}
                elif pending_headers:
                    # 有 pending headers 但无 EXTINF (纯URL行)
                    ch = Channel(name="", url="")
                    self._finalize_m3u_channel(ch, line, pending_headers)
                    if ch.name:
                        self._add_channel(ch)
                    pending_headers = {}

        return ""

    def _parse_extm3u_attrs(self, line: str):
        """解析 #EXTM3U 行的全局属性"""
        for match in _M3U_ATTR_RE.finditer(line):
            key = match.group(1)
            value = match.group(2)
            if key == "tvg-url":
                self.tvg_url = value
                if not self.epg_url:
                    self.epg_url = value
            elif key == "catchup":
                self.global_catchup = value
            elif key == "catchup-source":
                self.global_catchup_source = value
            elif key == "catchup-replace":
                self.global_catchup_replace = value

    def _parse_extinf(self, line: str) -> Channel:
        """解析 #EXTINF 行的频道属性"""
        attrs: Dict[str, str] = {}
        for match in _M3U_ATTR_RE.finditer(line):
            attrs[match.group(1)] = match.group(2)

        # 频道名在最后一个逗号后
        name = ""
        comma_idx = line.rfind(",")
        if comma_idx >= 0:
            name = line[comma_idx + 1:].strip()

        group_title = attrs.get("group-title", "") or "未分类"

        return Channel(
            name=name,
            url="",
            logo=attrs.get("tvg-logo", ""),
            group=group_title,
            tvg_id=attrs.get("tvg-id", ""),
            tvg_name=attrs.get("tvg-name", name),
            tvg_chno=attrs.get("tvg-chno", ""),
            http_user_agent=attrs.get("http-user-agent", ""),
            catchup_type=attrs.get("catchup", ""),
            catchup_source=attrs.get("catchup-source", ""),
            catchup_regex=attrs.get("catchup-regex", attrs.get("regex", "")),
            catchup_replace=attrs.get("catchup-replace", ""),
        )

    def _finalize_m3u_channel(self, channel: Channel, url_line: str,
                               pending_headers: Dict[str, str]):
        """完成 M3U 频道: 解析URL行, 合并标头, 应用全局catchup"""
        parsed = parse_inline_url(url_line)
        channel.url = parsed.url
        channel.multi_urls = list(parsed.multi_urls)

        # 合并标头: pending (EXTHTTP/EXTVLCOPT) + 行内 | 参数
        merged = dict(pending_headers)
        merged.update(parsed.headers)

        # http_user_agent 优先级: EXTINF 属性 > 行内 | params > EXTHTTP/EXTVLCOPT
        if channel.http_user_agent:
            # EXTINF http-user-agent 优先
            merged["User-Agent"] = channel.http_user_agent
        elif parsed.http_user_agent:
            channel.http_user_agent = parsed.http_user_agent
        elif "User-Agent" in merged:
            channel.http_user_agent = merged["User-Agent"]
        channel.headers = merged

        if parsed.parse:
            channel.parse = parsed.parse
        if parsed.format:
            channel.format = parsed.format
        if parsed.click:
            channel.click = parsed.click
        if parsed.force_key:
            channel.force_key = parsed.force_key

        # 应用全局 catchup 默认值
        self._apply_global_catchup(channel)

        self._add_channel(channel)

    # ---------- TXT 解析 ----------

    def _parse_txt(self, text: str) -> str:
        """解析 TXT 格式 (#genre# 分组, 含密码保护)"""
        self._clear()

        current_group = "未分类"
        current_password = ""

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if "#genre#" in line.lower():
                # 分组行: "名称,#genre#" 或 "名称_密码,#genre#"
                parts = line.split(",")
                raw_group = parts[0].strip() if parts else ""
                display_name, password = parse_group_password(raw_group)
                current_group = display_name or "未分类"
                current_password = password
                self._ensure_group(current_group, current_password)
                continue

            # 频道行: "频道名,URL"
            parts = line.split(",", 1)
            if len(parts) != 2:
                continue
            name = parts[0].strip()
            raw_url = parts[1].strip()
            if not name or not raw_url:
                continue

            parsed = parse_inline_url(raw_url)
            channel = Channel(
                name=name,
                url=parsed.url,
                group=current_group,
                headers=dict(parsed.headers),
                http_user_agent=parsed.http_user_agent,
                parse=parsed.parse,
                format=parsed.format,
                click=parsed.click,
                force_key=parsed.force_key,
                multi_urls=list(parsed.multi_urls),
            )

            # 分组密码 (用于结构化 Group)
            self._ensure_group(current_group, current_password)
            self._apply_global_catchup(channel)
            self._add_channel(channel)

        return ""

    # ---------- JSON 解析 ----------

    def _parse_json(self, text: str) -> str:
        """解析 JSON 格式 (TVBox lives 格式)"""
        self._clear()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return f"JSON 解析失败: {e}"

        # 兼容多种 JSON 结构
        if isinstance(data, dict):
            groups_data = data.get("groups") or data.get("lives") or data.get("list") or []
            if isinstance(groups_data, dict):
                groups_data = [groups_data]
            elif not isinstance(groups_data, list):
                groups_data = [data]  # 单个分组对象
        elif isinstance(data, list):
            groups_data = data
        else:
            return "JSON 格式不支持"

        for grp in groups_data:
            if not isinstance(grp, dict):
                continue
            raw_name = grp.get("name", grp.get("group", grp.get("group-title", ""))) or "未分类"
            display_name, password = parse_group_password(raw_name)
            self._ensure_group(display_name, password)

            channels = grp.get("channels", grp.get("list", grp.get("urls", [])))
            self._add_json_channels(channels, display_name, {})

        return ""

    def _add_json_channels(self, channels, group_name: str,
                           source_headers: Dict[str, str]):
        """将 JSON 频道数据添加到解析器"""
        if isinstance(channels, dict):
            # {"频道名": "url"} 格式
            for name, url in channels.items():
                ch = self._create_channel_from_json(name, url, {}, group_name)
                if ch:
                    if source_headers:
                        self._merge_headers(ch, source_headers)
                    self._apply_global_catchup(ch)
                    self._add_channel(ch)
        elif isinstance(channels, list):
            for ch_data in channels:
                if isinstance(ch_data, dict):
                    name = ch_data.get("name", ch_data.get("title", ""))
                    urls = ch_data.get("urls") or ch_data.get("url") or ch_data.get("urls", "")
                    ch = self._create_channel_from_json(name, urls, ch_data, group_name)
                elif isinstance(ch_data, (list, tuple)) and len(ch_data) >= 2:
                    ch = self._create_channel_from_json(
                        str(ch_data[0]), str(ch_data[1]), {}, group_name)
                else:
                    continue
                if ch:
                    if source_headers:
                        self._merge_headers(ch, source_headers)
                    self._apply_global_catchup(ch)
                    self._add_channel(ch)

    def _create_channel_from_json(self, name: str, urls, ch_data: dict,
                                   group_name: str) -> Optional[Channel]:
        """从 JSON 频道数据创建 Channel"""
        if not name:
            return None

        logo = ""
        tvg_id = ""
        tvg_name = ""
        tvg_chno = ""
        catchup_type = ""
        catchup_source = ""
        catchup_regex = ""
        catchup_replace = ""
        parse_flag = 0
        fmt = ""
        click = ""
        force_key = ""
        headers: Dict[str, str] = {}

        if isinstance(ch_data, dict):
            logo = ch_data.get("logo", ch_data.get("tvg-logo", ""))
            tvg_id = ch_data.get("tvg-id", ch_data.get("tvg_id", ""))
            tvg_name = ch_data.get("tvg-name", ch_data.get("tvg_name", name))
            tvg_chno = ch_data.get("tvg-chno", ch_data.get("tvg_chno", ""))
            catchup_type = ch_data.get("catchup", ch_data.get("catchup-type", ""))
            catchup_source = ch_data.get("catchup-source", "")
            catchup_regex = ch_data.get("catchup-regex", ch_data.get("regex", ""))
            catchup_replace = ch_data.get("catchup-replace", "")
            fmt = ch_data.get("format", "")
            click = ch_data.get("click", "")
            force_key = ch_data.get("forceKey", ch_data.get("force_key", ""))
            parse_flag = ch_data.get("parse", 0) or 0
            # 请求头
            if ch_data.get("ua"):
                headers["User-Agent"] = ch_data["ua"]
            if ch_data.get("origin"):
                headers["Origin"] = ch_data["origin"]
            if ch_data.get("referer"):
                headers["Referer"] = ch_data["referer"]
            hdr = ch_data.get("header") or ch_data.get("headers")
            if isinstance(hdr, dict):
                headers.update({str(k): str(v) for k, v in hdr.items()})
            elif isinstance(hdr, str) and hdr.startswith("{"):
                try:
                    parsed_hdr = json.loads(hdr)
                    if isinstance(parsed_hdr, dict):
                        headers.update({str(k): str(v) for k, v in parsed_hdr.items()})
                except json.JSONDecodeError:
                    pass

        # 处理 URL (字符串或列表)
        primary_url = ""
        multi_urls: List[str] = []

        if isinstance(urls, list):
            valid_urls = [str(u).strip() for u in urls if u]
            if valid_urls:
                parsed = parse_inline_url(valid_urls[0])
                primary_url = parsed.url
                multi_urls = parsed.multi_urls
                for u in valid_urls[1:]:
                    p = parse_inline_url(u)
                    if p.url:
                        multi_urls.append(p.url)
                # 合并行内标头
                headers.update(parsed.headers)
        elif isinstance(urls, str):
            parsed = parse_inline_url(urls)
            primary_url = parsed.url
            multi_urls = parsed.multi_urls
            headers.update(parsed.headers)
            if parsed.format:
                fmt = parsed.format
            if parsed.parse:
                parse_flag = parsed.parse
            if parsed.click:
                click = parsed.click
            if parsed.force_key:
                force_key = parsed.force_key
        else:
            return None

        if not primary_url and not multi_urls:
            return None

        return Channel(
            name=name,
            url=primary_url,
            logo=logo,
            group=group_name,
            tvg_id=tvg_id,
            tvg_name=tvg_name or name,
            tvg_chno=tvg_chno,
            headers=headers,
            catchup_type=catchup_type,
            catchup_source=catchup_source,
            catchup_regex=catchup_regex,
            catchup_replace=catchup_replace,
            parse=parse_flag,
            format=fmt,
            click=click,
            force_key=force_key,
            multi_urls=multi_urls,
        )

    # ---------- Catchup 时移 ----------

    def _apply_global_catchup(self, channel: Channel):
        """将全局 catchup 默认值应用到频道 (仅当频道未设置时)"""
        if not channel.catchup_type and self.global_catchup:
            channel.catchup_type = self.global_catchup
        if not channel.catchup_source and self.global_catchup_source:
            channel.catchup_source = self.global_catchup_source
        if not channel.catchup_replace and self.global_catchup_replace:
            channel.catchup_replace = self.global_catchup_replace

    def build_catchup_url(self, channel: Channel, start_ts: int,
                           end_ts: int) -> str:
        """构建 Catchup 时移 URL

        start_ts / end_ts: Unix 时间戳 (秒)
        返回空字符串表示不支持 catchup
        """
        catchup_type = channel.catchup_type or self.global_catchup
        catchup_source = channel.catchup_source or self.global_catchup_source
        catchup_replace = channel.catchup_replace or self.global_catchup_replace

        if not catchup_source:
            return ""

        # regex 匹配判断是否适用
        if channel.catchup_regex:
            try:
                if not re.search(channel.catchup_regex, channel.url):
                    return ""
            except re.error:
                pass

        # 应用 replace 替换对
        base_url = channel.url
        if catchup_replace:
            tokens = [t for t in catchup_replace.split(",")]
            for i in range(0, len(tokens) - 1, 2):
                search = tokens[i]
                repl = tokens[i + 1]
                base_url = base_url.replace(search, repl)

        # 替换时间变量
        url = self._substitute_time_vars(catchup_source, start_ts, end_ts)

        if catchup_type == "append":
            url = base_url + url
        # default: 完全替换, 使用 url

        return url

    def _substitute_time_vars(self, template: str, start_ts: int,
                               end_ts: int) -> str:
        """替换 Catchup 模板中的时间变量
        {(b)fmt} -> 开始时间 (strftime fmt)
        {(e)fmt} -> 结束时间 (strftime fmt)
        {utc} / {utc:offset} -> 开始 Unix 秒
        {utcend} / {utcend:offset} -> 结束 Unix 秒
        """
        tz = self._get_tz()
        try:
            start_dt = datetime.fromtimestamp(start_ts, tz=tz)
            end_dt = datetime.fromtimestamp(end_ts, tz=tz)
        except (OSError, OverflowError, ValueError):
            start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
            end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)

        def _repl_b(m):
            fmt = m.group(1)
            try:
                return start_dt.strftime(fmt) if fmt else str(start_ts)
            except (ValueError, OverflowError):
                return str(start_ts)

        def _repl_e(m):
            fmt = m.group(1)
            try:
                return end_dt.strftime(fmt) if fmt else str(end_ts)
            except (ValueError, OverflowError):
                return str(end_ts)

        def _repl_utc(m):
            offset_str = m.group(1) or ""
            if offset_str:
                try:
                    offset_hours = int(offset_str)
                    return str(start_ts + offset_hours * 3600)
                except ValueError:
                    pass
            return str(start_ts)

        def _repl_utcend(m):
            offset_str = m.group(1) or ""
            if offset_str:
                try:
                    offset_hours = int(offset_str)
                    return str(end_ts + offset_hours * 3600)
                except ValueError:
                    pass
            return str(end_ts)

        result = _CATCHUP_B_RE.sub(_repl_b, template)
        result = _CATCHUP_E_RE.sub(_repl_e, result)
        result = _CATCHUP_UTC_RE.sub(_repl_utc, result)
        result = _CATCHUP_UTCEND_RE.sub(_repl_utcend, result)
        return result

    def _get_tz(self):
        """获取时区对象"""
        tz_str = self.time_zone or ""
        if not tz_str:
            return timezone(timedelta(hours=0))
        # 数字偏移: +8, -5, 8
        try:
            hours = int(tz_str)
            return timezone(timedelta(hours=hours))
        except ValueError:
            pass
        # 命名时区 (Python 3.9+ zoneinfo)
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tz_str)
        except ImportError:
            pass
        except Exception:
            pass
        return timezone(timedelta(hours=0))

    # ---------- 引擎识别 ----------

    def is_tvbus(self, url: str) -> bool:
        """判断是否为 TVBus 格式"""
        return url_is_tvbus(url)

    def is_forcetech(self, url: str) -> bool:
        """判断是否为 ForceTech 格式"""
        return url_is_forcetech(url)

    # ---------- 内部工具 ----------

    def _clear(self):
        """清空所有数据"""
        self.groups.clear()
        self.group_list.clear()
        self.channels.clear()
        self.tvg_url = ""
        self.global_catchup = ""
        self.global_catchup_source = ""
        self.global_catchup_replace = ""

    def _ensure_group(self, name: str, password: str = "") -> Group:
        """确保分组存在, 返回 Group 对象"""
        if name not in self.groups:
            self.groups[name] = []
        for g in self.group_list:
            if g.name == name:
                if password and not g.password:
                    g.password = password
                return g
        g = Group(name=name, password=password)
        self.group_list.append(g)
        return g

    def _add_channel(self, channel: Channel):
        """添加频道到列表和分组"""
        self.channels.append(channel)
        if channel.group not in self.groups:
            self.groups[channel.group] = []
        self.groups[channel.group].append(channel)
        # 添加到结构化分组
        target_group = None
        for g in self.group_list:
            if g.name == channel.group:
                target_group = g
                break
        if not target_group:
            target_group = Group(name=channel.group)
            self.group_list.append(target_group)
        target_group.channels.append(channel)

    def _merge_headers(self, channel: Channel, extra: Dict[str, str]):
        """合并请求头到频道 (频道已有的优先)"""
        merged = dict(extra)
        merged.update(channel.headers)
        channel.headers = merged
        if not channel.http_user_agent and "User-Agent" in merged:
            channel.http_user_agent = merged["User-Agent"]

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        """序列化为前端可用的结构"""
        return {
            # 向后兼容字段
            "groups": {
                g: [{"name": c.name, "url": c.url, "logo": c.logo}
                     for c in channels]
                for g, channels in self.groups.items()
            },
            "group_names": list(self.groups.keys()),
            "channel_count": len(self.channels),
            "epg": self.epg_url,
            # 新增结构化字段
            "group_list": [g.to_dict() for g in self.group_list],
            "channels": [c.to_dict() for c in self.channels],
            "tvg_url": self.tvg_url,
            "global_catchup": self.global_catchup,
            "global_catchup_source": self.global_catchup_source,
            "time_zone": self.time_zone,
            "boot": self.boot,
            "skip_password": self.skip_password,
        }


# ======================== 辅助函数 ========================

def _obj_to_dict(obj: Any) -> dict:
    """将对象转换为 dict (用于 LiveSourceConfig.from_dict)"""
    if isinstance(obj, dict):
        return obj
    result = {}
    for attr in ("name", "type", "url", "epg", "logo", "api", "ext",
                 "ua", "origin", "referer", "timeZone", "timezone",
                 "boot", "pass", "groups"):
        if hasattr(obj, attr):
            result[attr] = getattr(obj, attr)
    return result
