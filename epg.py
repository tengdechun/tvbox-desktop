"""
EPG 节目单解析器 —— 完整实现 FongMi/TV 兼容的 EPG 功能

支持特性:
  - XMLTV 格式 (.xml / .xml.gz)
  - API 模板模式: URL 支持 {id}, {name}, {epg} 变量替换
  - 多 EPG 来源: 逗号分隔多个 URL, 合并所有来源节目数据
  - 定时刷新: 每 6 小时自动刷新 (threading.Timer)
  - 频道匹配增强:
      * tvg-id 精确匹配
      * tvg-name 模糊匹配
      * 频道名去台标后匹配 (如 "CCTV-1" 匹配 "CCTV1")
      * 频道别名映射
  - 节目时间信息: XMLTV 时间解析 (yyyyMMddHHmmss), 时区调整
  - 缓存机制: 解析结果缓存, 手动清除

Python 3.8+ 兼容
"""

import os
import re
import gzip
import time
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

import requests


# 刷新间隔 (秒): 6 小时
_REFRESH_INTERVAL = 6 * 3600

# 模板变量正则 (不在 f-string 中, 避免反斜杠问题)
_TEMPLATE_VAR_RE = re.compile(r"\{(id|name|epg)\}")


# ======================== 数据结构 ========================

@dataclass
class Programme:
    """节目"""
    start: str = ""           # 原始开始时间 (XMLTV 格式)
    stop: str = ""           # 原始结束时间
    title: str = ""
    desc: str = ""
    channel_id: str = ""
    category: str = ""       # 分类
    subtitle: str = ""       # 副标题
    date: str = ""           # 日期
    icon: str = ""           # 图标
    start_ts: int = 0        # 开始 Unix 时间戳
    stop_ts: int = 0         # 结束 Unix 时间戳

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "stop": self.stop,
            "title": self.title,
            "desc": self.desc,
            "channel_id": self.channel_id,
            "category": self.category,
            "subtitle": self.subtitle,
            "date": self.date,
            "icon": self.icon,
            "start_ts": self.start_ts,
            "stop_ts": self.stop_ts,
        }


@dataclass
class EpgChannel:
    """EPG 频道"""
    tvg_id: str = ""
    tvg_name: str = ""
    icon: str = ""
    url: str = ""
    names: List[str] = field(default_factory=list)  # 所有 display-name

    def to_dict(self) -> dict:
        return {
            "tvg_id": self.tvg_id,
            "tvg_name": self.tvg_name,
            "icon": self.icon,
            "url": self.url,
            "names": list(self.names),
        }


# ======================== 工具函数 ========================

def _strip_logo(name: str) -> str:
    """去除频道名中的台标符号 (- _ 空格 等), 用于模糊匹配
    例: "CCTV-1" -> "CCTV1", "CCTV_1" -> "CCTV1", "CCTV 1" -> "CCTV1"
    """
    if not name:
        return ""
    return name.replace("-", "").replace("_", "").replace(" ", "").strip()


def _is_template_url(url: str) -> bool:
    """判断 URL 是否为模板 (包含 {id}, {name}, {epg} 变量)"""
    if not url:
        return False
    return bool(_TEMPLATE_VAR_RE.search(url))


def _parse_xmltv_time(time_str: str) -> int:
    """解析 XMLTV 时间格式: yyyyMMddHHmmss [+-]zzzz
    返回 Unix 时间戳 (秒), 失败返回 0
    """
    if not time_str:
        return 0
    time_str = time_str.strip()
    parts = time_str.split()
    dt_str = parts[0]
    tz_str = parts[1] if len(parts) > 1 else ""

    # 尝试不同精度
    dt = None
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d"):
        try:
            dt = datetime.strptime(dt_str, fmt)
            break
        except ValueError:
            continue

    if dt is None:
        return 0

    # 应用时区
    if tz_str and len(tz_str) >= 5:
        try:
            sign = 1 if tz_str[0] == "+" else -1
            hours = int(tz_str[1:3])
            minutes = int(tz_str[3:5])
            offset = timedelta(hours=hours, minutes=minutes)
            tz = timezone(sign * offset)
            dt = dt.replace(tzinfo=tz)
        except (ValueError, IndexError):
            pass

    try:
        return int(dt.timestamp())
    except (OSError, OverflowError, ValueError):
        return 0


# ======================== 解析器 ========================

class EpgParser:
    """XMLTV / API 模板 EPG 解析器"""

    def __init__(self):
        # channel_id -> [Programme] (主缓存)
        self._cache: Dict[str, List[Programme]] = {}
        # channel_id -> EpgChannel
        self._channels: Dict[str, EpgChannel] = {}
        # display_name -> channel_id (精确索引)
        self._name_index: Dict[str, str] = {}
        # 频道别名: alias -> channel_id
        self._aliases: Dict[str, str] = {}

        self._load_time: int = 0
        self._urls: List[str] = []          # 所有 EPG URL (含模板)
        self._urls_str: str = ""            # 原始 URL 字符串
        self._has_template: bool = False     # 是否含模板 URL
        self._timezone: str = ""            # 时区设置

        self._lock = threading.Lock()
        self._refresh_thread: Optional[threading.Thread] = None
        self._refresh_timer: Optional[threading.Timer] = None
        self._loading: bool = False

    # ---------- 加载 ----------

    def load(self, url: str) -> str:
        """加载 EPG (URL 或文件路径), 返回错误信息
        支持逗号分隔多个 URL, 支持 {id}/{name}/{epg} 模板
        """
        self._urls_str = url or ""
        urls = [u.strip() for u in (url or "").split(",") if u.strip()]
        self._urls = urls
        self._has_template = any(_is_template_url(u) for u in urls)

        # 非模板 URL 立即加载; 模板 URL 按需加载
        errors = []
        loaded_any = False

        with self._lock:
            if not self._has_template:
                # 全部是非模板: 清空后重新加载
                self._cache.clear()
                self._channels.clear()
                self._name_index.clear()
            self._load_time = int(time.time())

        for u in urls:
            if _is_template_url(u):
                # 模板 URL: 按需加载, 不在此处获取
                continue
            err = self._load_single(u)
            if err:
                errors.append(err)
            else:
                loaded_any = True

        # 调度定时刷新
        self._schedule_refresh()

        if errors and not loaded_any:
            return "; ".join(errors)
        return ""

    def load_async(self, url: str):
        """异步加载 EPG"""
        if self._loading:
            return
        self._loading = True
        self._refresh_thread = threading.Thread(
            target=self._async_load, args=(url,), daemon=True
        )
        self._refresh_thread.start()

    def _async_load(self, url: str):
        try:
            self.load(url)
        finally:
            self._loading = False

    def _load_single(self, url: str) -> str:
        """加载单个 EPG 来源 (文件或URL)"""
        try:
            if os.path.exists(url):
                with open(url, "rb") as f:
                    data = f.read()
            else:
                resp = requests.get(url, timeout=30, headers={
                    "User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"
                })
                data = resp.content

            # 解压 gzip
            if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
                try:
                    data = gzip.decompress(data)
                except (OSError, gzip.BadGzipFile):
                    pass

            return self._parse(data)
        except Exception as e:
            return f"EPG 加载失败 ({url}): {e}"

    # ---------- 解析 ----------

    def _parse(self, data: bytes) -> str:
        """解析 XMLTV XML 数据"""
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            return f"XML 解析失败: {e}"
        except Exception as e:
            return f"解析失败: {e}"

        with self._lock:
            # 解析频道
            for ch_el in root.iter("channel"):
                ch_id = ch_el.get("id", "")
                if not ch_id:
                    continue

                epg_ch = self._channels.get(ch_id, EpgChannel(tvg_id=ch_id))
                epg_ch.tvg_id = ch_id

                # display-name (可能有多个)
                for name_el in ch_el.iter("display-name"):
                    if name_el.text:
                        dn = name_el.text.strip()
                        if dn not in epg_ch.names:
                            epg_ch.names.append(dn)
                        if not epg_ch.tvg_name:
                            epg_ch.tvg_name = dn
                        # 建立名称索引
                        self._name_index[dn] = ch_id

                # icon
                icon_el = ch_el.find("icon")
                if icon_el is not None:
                    epg_ch.icon = icon_el.get("src", "")

                # url
                url_el = ch_el.find("url")
                if url_el is not None and url_el.text:
                    epg_ch.url = url_el.text.strip()

                self._channels[ch_id] = epg_ch

            # 解析节目表
            for prog_el in root.iter("programme"):
                ch_id = prog_el.get("channel", "")
                if not ch_id:
                    continue

                p = Programme(channel_id=ch_id)
                p.start = prog_el.get("start", "")
                p.stop = prog_el.get("stop", "")
                p.start_ts = _parse_xmltv_time(p.start)
                p.stop_ts = _parse_xmltv_time(p.stop)

                # 标题
                title_el = prog_el.find("title")
                if title_el is not None:
                    p.title = (title_el.text or "").strip()

                # 描述
                desc_el = prog_el.find("desc")
                if desc_el is not None:
                    p.desc = (desc_el.text or "").strip()

                # 分类
                cat_el = prog_el.find("category")
                if cat_el is not None:
                    p.category = (cat_el.text or "").strip()

                # 副标题
                sub_el = prog_el.find("sub-title")
                if sub_el is not None:
                    p.subtitle = (sub_el.text or "").strip()

                # 日期
                date_el = prog_el.find("date")
                if date_el is not None:
                    p.date = (date_el.text or "").strip()

                # 图标
                icon_el = prog_el.find("icon")
                if icon_el is not None:
                    p.icon = icon_el.get("src", "")

                if ch_id not in self._cache:
                    self._cache[ch_id] = []
                self._cache[ch_id].append(p)

            # 按时间排序
            for ch_id in self._cache:
                self._cache[ch_id].sort(key=lambda p: p.start_ts or 0)

        return ""

    # ---------- 模板 API 按需加载 ----------

    def _fetch_template_epg(self, template_url: str, channel_id: str,
                              channel_name: str, tvg_id: str) -> str:
        """按模板 URL 获取单个频道的 EPG 数据
        变量: {id}=tvg_id, {name}=channel_name, {epg}=tvg_id/channel_id
        """
        url = template_url
        url = url.replace("{id}", tvg_id or channel_id or "")
        url = url.replace("{name}", channel_name or "")
        url = url.replace("{epg}", tvg_id or channel_id or "")

        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"
            })
            data = resp.content
            if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
                try:
                    data = gzip.decompress(data)
                except (OSError, gzip.BadGzipFile):
                    pass
            return self._parse(data)
        except Exception as e:
            return f"模板 EPG 获取失败: {e}"

    # ---------- 频道匹配 ----------

    def match_channel(self, channel_name: str) -> Optional[str]:
        """通过频道名匹配 EPG channel_id
        匹配优先级:
          1. 别名映射 (alias)
          2. tvg-id 精确匹配
          3. display-name 精确匹配
          4. tvg-name 精确匹配
          5. 去台标后匹配 (CCTV-1 -> CCTV1)
          6. 模糊包含匹配
        """
        if not channel_name:
            return None

        name = channel_name.strip()
        name_lower = name.lower()
        name_stripped = _strip_logo(name)
        name_stripped_lower = name_stripped.lower()

        with self._lock:
            # 1. 别名映射
            if name in self._aliases:
                return self._aliases[name]
            if name_stripped in self._aliases:
                return self._aliases[name_stripped]

            # 2. tvg-id 精确匹配
            if name in self._channels:
                return name
            if name_lower in self._channels:
                return name_lower

            # 3. display-name 精确匹配
            if name in self._name_index:
                return self._name_index[name]
            if name_lower in self._name_index:
                return self._name_index[name_lower]

            # 4. tvg_name 精确匹配 (遍历)
            for ch_id, epg_ch in self._channels.items():
                if epg_ch.tvg_name and epg_ch.tvg_name.lower() == name_lower:
                    return ch_id

            # 5. 去台标后匹配
            if name_stripped:
                for ch_id, epg_ch in self._channels.items():
                    if epg_ch.tvg_name and _strip_logo(epg_ch.tvg_name).lower() == name_stripped_lower:
                        return ch_id
                for dn, ch_id in self._name_index.items():
                    if _strip_logo(dn).lower() == name_stripped_lower:
                        return ch_id

            # 6. 模糊包含匹配
            for dn, ch_id in self._name_index.items():
                dn_lower = dn.lower()
                if name_lower in dn_lower or dn_lower in name_lower:
                    return ch_id

        # 7. 模板模式: 按需获取
        if self._has_template:
            return self._match_template(name)

        return None

    def _match_template(self, channel_name: str) -> Optional[str]:
        """模板模式: 尝试用频道名获取 EPG, 缓存后返回 channel_name 作为 key"""
        for url in self._urls:
            if not _is_template_url(url):
                continue
            # 尝试用频道名获取
            err = self._fetch_template_epg(url, channel_name, channel_name, channel_name)
            if not err:
                # 检查是否有节目数据
                with self._lock:
                    if channel_name in self._cache and self._cache[channel_name]:
                        return channel_name
            # 也尝试建立 channel 映射
            with self._lock:
                for ch_id in list(self._cache.keys()):
                    if ch_id not in self._channels:
                        self._channels[ch_id] = EpgChannel(tvg_id=ch_id, tvg_name=channel_name)
                        self._name_index[channel_name] = ch_id
                        return ch_id
        return None

    def set_alias(self, alias: str, channel_id: str):
        """设置频道别名映射"""
        with self._lock:
            self._aliases[alias.strip()] = channel_id

    def set_aliases(self, aliases: Dict[str, str]):
        """批量设置别名映射"""
        with self._lock:
            for alias, ch_id in aliases.items():
                self._aliases[alias.strip()] = ch_id

    # ---------- 节目查询 ----------

    def get_programmes(self, channel_id: str) -> List[dict]:
        """获取指定频道所有节目 (按时间排序)
        channel_id: EPG channel_id (来自 match_channel) 或频道名
        返回: [{start, stop, title, desc, category, subtitle, date, icon, ...}]
        """
        # 模板模式: 按需获取
        if self._has_template and channel_id not in self._cache:
            for url in self._urls:
                if _is_template_url(url):
                    self._fetch_template_epg(url, channel_id, channel_id, channel_id)
                    break

        with self._lock:
            progs = self._cache.get(channel_id, [])
            return [p.to_dict() for p in progs]

    def get_current_programme(self, channel_id: str) -> Optional[dict]:
        """获取当前正在播放的节目"""
        now_ts = int(time.time())

        # 模板模式: 按需获取
        if self._has_template and channel_id not in self._cache:
            for url in self._urls:
                if _is_template_url(url):
                    self._fetch_template_epg(url, channel_id, channel_id, channel_id)
                    break

        with self._lock:
            progs = self._cache.get(channel_id, [])
            for p in progs:
                start = p.start_ts or 0
                stop = p.stop_ts or 9999999999
                if start <= now_ts <= stop:
                    return p.to_dict()
        return None

    def get_programmes_by_time(self, channel_id: str,
                                start_ts: int, end_ts: int) -> List[dict]:
        """获取指定时间范围内的节目"""
        with self._lock:
            progs = self._cache.get(channel_id, [])
            return [p.to_dict() for p in progs
                    if p.start_ts and p.start_ts >= start_ts
                    and (not p.stop_ts or p.stop_ts <= end_ts)]

    def get_channel_name(self, channel_id: str) -> str:
        """获取频道显示名"""
        with self._lock:
            ch = self._channels.get(channel_id)
            if ch:
                return ch.tvg_name or (ch.names[0] if ch.names else channel_id)
        return channel_id

    def get_channel_info(self, channel_id: str) -> Optional[dict]:
        """获取频道完整信息"""
        with self._lock:
            ch = self._channels.get(channel_id)
            if ch:
                return ch.to_dict()
        return None

    def get_all_channels(self) -> List[dict]:
        """获取所有 EPG 频道"""
        with self._lock:
            return [ch.to_dict() for ch in self._channels.values()]

    # ---------- 缓存管理 ----------

    def clear_cache(self):
        """手动清除缓存"""
        with self._lock:
            self._cache.clear()
            self._channels.clear()
            self._name_index.clear()
            self._aliases.clear()
            self._load_time = 0

    def cancel_refresh(self):
        """取消定时刷新"""
        if self._refresh_timer:
            self._refresh_timer.cancel()
            self._refresh_timer = None

    # ---------- 定时刷新 ----------

    def _schedule_refresh(self):
        """调度定时刷新 (每 6 小时)"""
        self.cancel_refresh()
        self._refresh_timer = threading.Timer(
            _REFRESH_INTERVAL, self._do_refresh
        )
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def _do_refresh(self):
        """执行定时刷新"""
        if not self._urls_str:
            return
        try:
            self.load(self._urls_str)
        except Exception:
            pass

    # ---------- 时区 ----------

    def set_timezone(self, tz_str: str):
        """设置时区 (用于时间调整, 暂存)"""
        self._timezone = tz_str or ""

    def get_timezone(self) -> str:
        return self._timezone

    # ---------- 摘要 ----------

    def to_dict(self) -> dict:
        """摘要信息"""
        with self._lock:
            return {
                "channel_count": len(self._channels),
                "programme_count": sum(len(v) for v in self._cache.values()),
                "load_time": self._load_time,
                "url": self._urls_str,
                "urls": list(self._urls),
                "has_template": self._has_template,
                "is_loading": self._loading,
                "alias_count": len(self._aliases),
            }
