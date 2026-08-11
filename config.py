"""
TVBox 配置解析器 —— 数据模型与配置加载
兼容 TVBoxOSC / FongMi TV 的 JSON 配置格式
完整复刻原版 FongMi/TV 的所有配置字段
"""

import json
import os
import re
import requests
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union


# ======== 辅助数据类 ========

@dataclass
class Style:
    """站点分类展示样式
    type: rect(矩形) / oval(椭圆) / list(列表)
    ratio: 宽高比 0.75=3:4, 1=1:1, 1.33=4:3, 1.78=16:9
    """
    type: str = "rect"
    ratio: float = 1.78

    @classmethod
    def from_dict(cls, data: Any) -> "Style":
        if not data:
            return cls()
        if isinstance(data, dict):
            return cls(
                type=data.get("type", "rect"),
                ratio=float(data.get("ratio", 1.78)),
            )
        return cls()

    def to_dict(self) -> dict:
        return {"type": self.type, "ratio": self.ratio}


@dataclass
class Catchup:
    """直播回看(catchup)配置
    type: append(追加) / default(默认)
    source: URL模板, 支持 {(b)格式}开始时间, {(e)格式}结束时间,
            {utc:偏移}开始Unix秒, {utcend:偏移}结束Unix秒
    regex: 用于从播放URL中提取变量的正则
    replace: 逗号分隔的替换对, 如 "key1,val1,key2,val2"
    """
    type: str = "default"
    source: str = ""
    regex: str = ""
    replace: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> "Catchup":
        if not data:
            return cls()
        if isinstance(data, dict):
            return cls(
                type=data.get("type", "default"),
                source=data.get("source", ""),
                regex=data.get("regex", ""),
                replace=data.get("replace", ""),
            )
        return cls()

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "source": self.source,
            "regex": self.regex,
            "replace": self.replace,
        }

    def build_source(self, start_utc: int, end_utc: int,
                     start_fmt: str = "", end_fmt: str = "") -> str:
        """根据时间变量构建 catchup source URL"""
        result = self.source
        if start_fmt:
            result = result.replace("{(b)" + start_fmt + "}", start_fmt)
        if end_fmt:
            result = result.replace("{(e)" + end_fmt + "}", end_fmt)
        # {utc:offset} -> 开始时间 Unix 秒 + 偏移
        result = re.sub(
            r"\{utc:(-?\d+)\}",
            lambda m: str(start_utc + int(m.group(1))),
            result,
        )
        result = re.sub(
            r"\{utcend:(-?\d+)\}",
            lambda m: str(end_utc + int(m.group(1))),
            result,
        )
        # 无偏移的简写
        result = result.replace("{utc}", str(start_utc))
        result = result.replace("{utcend}", str(end_utc))
        return result


@dataclass
class Doh:
    """DNS over HTTPS 配置
    name: 名称
    url: DoH 服务 URL
    ips: bootstrap IP 列表(用于首次解析 DoH 服务器自身)
    """
    name: str = ""
    url: str = ""
    ips: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "Doh":
        if not data:
            return cls()
        if isinstance(data, dict):
            return cls(
                name=data.get("name", ""),
                url=data.get("url", ""),
                ips=data.get("ips", []),
            )
        return cls()

    def to_dict(self) -> dict:
        return {"name": self.name, "url": self.url, "ips": self.ips}


@dataclass
class Proxy:
    """代理配置
    type: http / https / socks4 / socks5
    host: 代理服务器地址
    port: 代理端口
    username / password: 认证信息
    rule: host 正则, 匹配的域名走此代理
    """
    name: str = ""
    type: str = "http"
    host: str = ""
    port: int = 0
    username: str = ""
    password: str = ""
    rule: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> "Proxy":
        if not data:
            return cls()
        if isinstance(data, dict):
            return cls(
                name=data.get("name", ""),
                type=data.get("type", "http"),
                host=data.get("host", ""),
                port=int(data.get("port", 0)),
                username=data.get("username", ""),
                password=data.get("password", ""),
                rule=data.get("rule", ""),
            )
        return cls()

    @classmethod
    def from_url(cls, url: str) -> "Proxy":
        """从代理 URL 字符串解析, 如 socks5://user:pass@127.0.0.1:1080"""
        if not url:
            return cls()
        pattern = re.compile(
            r"^(https?|socks[45])://"
            r"(?:(\S+?):(\S+?)@)?"
            r"([\w.-]+):(\d+)/?$",
            re.IGNORECASE,
        )
        m = pattern.match(url)
        if m:
            return cls(
                type=m.group(1).lower(),
                username=m.group(2) or "",
                password=m.group(3) or "",
                host=m.group(4),
                port=int(m.group(5)),
            )
        return cls()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "rule": self.rule,
        }

    def to_url(self) -> str:
        """转换为代理 URL 字符串"""
        auth = ""
        if self.username:
            auth = self.username + ":" + self.password + "@"
        return f"{self.type}://{auth}{self.host}:{self.port}"

    def matches(self, host: str) -> bool:
        """检查指定 host 是否匹配此代理的规则"""
        if not self.rule:
            return False
        try:
            return bool(re.search(self.rule, host))
        except re.error:
            return False


@dataclass
class Rule:
    """网络拦截规则
    host: 正则匹配域名
    regex: URL 提取正则列表(可有多个), 用于从页面中提取真实播放地址
    script: 自动执行的 JS 脚本(如点击/关闭广告按钮)
    exclude: 排除的 URL 正则(匹配此正则的请求将被拦截)
    """
    host: str = ""
    regex: List[str] = field(default_factory=list)
    script: str = ""
    exclude: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> "Rule":
        if not data:
            return cls()
        if isinstance(data, dict):
            regex_val = data.get("regex", [])
            if isinstance(regex_val, str):
                regex_val = [regex_val]
            return cls(
                host=data.get("host", ""),
                regex=regex_val,
                script=data.get("script", ""),
                exclude=data.get("exclude", ""),
            )
        return cls()

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "regex": self.regex,
            "script": self.script,
            "exclude": self.exclude,
        }

    def matches_host(self, host: str) -> bool:
        if not self.host:
            return False
        try:
            return bool(re.search(self.host, host))
        except re.error:
            return False

    def should_exclude(self, url: str) -> bool:
        if not self.exclude:
            return False
        try:
            return bool(re.search(self.exclude, url))
        except re.error:
            return False


@dataclass
class Header:
    """响应头注入配置(CORS)
    host: 正则匹配域名
    headers: 要注入的响应头字典
    """
    host: str = ""
    headers: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> "Header":
        if not data:
            return cls()
        if isinstance(data, dict):
            return cls(
                host=data.get("host", ""),
                headers=data.get("headers", {}),
            )
        return cls()

    def to_dict(self) -> dict:
        return {"host": self.host, "headers": self.headers}

    def matches_host(self, host: str) -> bool:
        if not self.host:
            return False
        try:
            return bool(re.search(self.host, host))
        except re.error:
            return False


# ======== 主体数据类 ========

@dataclass
class Site:
    """点播站点
    type: 0=JSON API, 1=JAR, 3=Python, 4=JS
    """
    key: str
    name: str
    type: int = 0
    api: str = ""
    searchable: int = 1
    quickSearch: int = 1
    filterable: int = 0
    ext: str = ""
    jar: str = ""
    categories: List[str] = field(default_factory=list)
    player_url: str = ""
    # 补全字段
    click: str = ""           # 点击选择器(用于嗅探时自动点击播放按钮)
    hide: int = 0             # 是否在站点列表中隐藏
    timeout: int = 15         # 请求超时(秒)
    changeable: int = 1       # 是否允许切换线路(0=禁用, 1=允许)
    indexs: int = 1           # 是否在首页显示
    header: dict = field(default_factory=dict)  # 自定义请求头
    style: Style = field(default_factory=Style)  # 分类展示样式


@dataclass
class LiveSource:
    """直播源
    type: 0=M3U, 1=TXT(#genre#), 2=JSON
    """
    name: str
    type: int = 0
    url: str = ""
    epg: str = ""
    logo: str = ""
    # 补全字段
    api: str = ""             # 直播 API 地址(用于动态获取频道列表)
    ext: str = ""             # 扩展参数
    jar: str = ""             # JAR 依赖
    ua: str = ""              # 自定义 User-Agent
    origin: str = ""          # Origin 请求头
    referer: str = ""         # Referer 请求头
    timeZone: str = ""        # 时区(如 Asia/Shanghai)
    catchup: Catchup = field(default_factory=Catchup)  # 回看配置
    groups: str = ""          # 分组过滤(逗号分隔)
    boot: int = 0             # 是否开机自启(0=否, 1=是)
    pass_required: int = 0    # 是否需要密码(JSON中的"pass"字段)


@dataclass
class Parse:
    """解析器
    type 0: 嗅探(WebView拦截)
    type 1: JSON API
    type 2: JSON扩展(合并type=1送入JAR)
    type 3: JSON聚合(合并所有解析器送入JAR)
    type 4: 超级解析(并行尝试所有type=0/1)
    """
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
    vod_tag: str = ""         # 标识类型(如 short/film/play)

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
    """TVBox 配置管理 —— 完整复刻 FongMi/TV 配置字段"""

    def __init__(self):
        # 顶层配置字段
        self.spider: str = ""            # 全局 Spider JAR 路径/URL
        self.wallpaper: str = ""         # 壁纸图片/影片路径/URL
        self.logo: str = ""              # 应用 Logo
        self.notice: str = ""            # 启动公告
        self.sites: List[Site] = []
        self.lives: List[LiveSource] = []
        self.parses: List[Parse] = []
        self.doh: List[Doh] = []         # DNS over HTTPS
        self.proxy: str = ""             # 代理 URL(字符串形式, 兼容旧版)
        self.proxies: List[Proxy] = []   # 结构化代理列表(按 host 正则规则)
        self.rules: List[Rule] = []      # 网络拦截规则
        self.headers: List[Header] = []  # CORS 响应头注入
        self.hosts: List[dict] = []      # DNS 解析覆盖(支持通配符 *)
        self.flags: List[str] = []       # 平台标识旗标
        self.ads: List[str] = []         # 广告域名黑名单
        self.danmaku: str = ""           # 弹幕 API URL
        self.raw: dict = {}
        self._site_map: Dict[str, Site] = {}

    # ======== 加载方法 ========

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
            text = text.strip().lstrip("\ufeff")
            # 去掉单行注释
            text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
            # 去掉尾逗号
            text = re.sub(r",\s*([}\]])", r"\1", text)
            try:
                data = json.loads(text)
            except Exception as e:
                return f"JSON 解析失败: {e}"

        self.raw = data
        self.sites.clear()
        self.lives.clear()
        self.parses.clear()
        self._site_map.clear()
        self.doh.clear()
        self.proxies.clear()
        self.rules.clear()
        self.headers.clear()

        # 解析顶层简单字段
        self.spider = data.get("spider", "")
        self.wallpaper = data.get("wallpaper", "")
        self.logo = data.get("logo", "")
        self.notice = data.get("notice", "")
        self.danmaku = data.get("danmaku", "")
        self.flags = data.get("flags", [])
        self.ads = data.get("ads", [])
        self.hosts = data.get("hosts", [])

        # 解析代理: 支持字符串和列表两种格式
        proxy_val = data.get("proxy", "")
        if isinstance(proxy_val, str):
            self.proxy = proxy_val
        elif isinstance(proxy_val, list):
            self.proxies = [Proxy.from_dict(p) for p in proxy_val]
            # 同时设置第一个作为默认 proxy 字符串
            if self.proxies and self.proxies[0].host:
                self.proxy = self.proxies[0].to_url()
        elif isinstance(proxy_val, dict):
            p = Proxy.from_dict(proxy_val)
            self.proxies = [p]
            if p.host:
                self.proxy = p.to_url()

        # 解析 DoH
        for d in data.get("doh", []):
            self.doh.append(Doh.from_dict(d))

        # 解析网络拦截规则
        for r in data.get("rules", []):
            self.rules.append(Rule.from_dict(r))

        # 解析响应头注入
        for h in data.get("headers", []):
            self.headers.append(Header.from_dict(h))

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
                click=s.get("click", ""),
                hide=s.get("hide", 0),
                timeout=s.get("timeout", 15),
                changeable=s.get("changeable", 1),
                indexs=s.get("indexs", 1),
                header=s.get("header", {}),
                style=Style.from_dict(s.get("style")),
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
                api=l.get("api", ""),
                ext=l.get("ext", ""),
                jar=l.get("jar", ""),
                ua=l.get("ua", ""),
                origin=l.get("origin", ""),
                referer=l.get("referer", ""),
                timeZone=l.get("timeZone", ""),
                catchup=Catchup.from_dict(l.get("catchup")),
                groups=l.get("groups", ""),
                boot=l.get("boot", 0),
                pass_required=l.get("pass", 0),
            ))

        # 解析解析器
        for p in data.get("parses", []):
            self.parses.append(Parse(
                name=p.get("name", ""),
                type=p.get("type", 0),
                url=p.get("url", ""),
                ext=p.get("ext", {}),
            ))

        return ""

    # ======== 查询方法 ========

    def get_site(self, key: str) -> Optional[Site]:
        return self._site_map.get(key)

    def get_searchable_sites(self) -> List[Site]:
        return [s for s in self.sites if s.searchable == 1 and s.hide == 0]

    def get_visible_sites(self) -> List[Site]:
        return [s for s in self.sites if s.hide == 0]

    def get_live_by_name(self, name: str) -> Optional[LiveSource]:
        for l in self.lives:
            if l.name == name:
                return l
        return None

    def get_parses_by_type(self, parse_type: int) -> List[Parse]:
        """获取指定类型的解析器"""
        return [p for p in self.parses if p.type == parse_type]

    def find_proxy_for_host(self, host: str) -> Optional[Proxy]:
        """根据 host 查找匹配的代理配置"""
        for p in self.proxies:
            if p.matches(host):
                return p
        # 如果有全局代理字符串, 返回解析后的 Proxy
        if self.proxy:
            return Proxy.from_url(self.proxy)
        return None

    def find_rule_for_host(self, host: str) -> Optional[Rule]:
        """根据 host 查找匹配的网络拦截规则"""
        for r in self.rules:
            if r.matches_host(host):
                return r
        return None

    def find_headers_for_host(self, host: str) -> Dict[str, str]:
        """根据 host 查找需要注入的响应头"""
        result = {}
        for h in self.headers:
            if h.matches_host(host):
                result.update(h.headers)
        return result

    def is_ad_domain(self, host: str) -> bool:
        """判断域名是否在广告黑名单中"""
        for ad in self.ads:
            if ad in host:
                return True
        return False

    def resolve_host(self, host: str) -> Optional[str]:
        """DNS 解析覆盖: 根据 hosts 配置返回自定义 IP"""
        for h in self.hosts:
            if isinstance(h, dict):
                pattern = h.get("host", "")
                ip = h.get("ip", "")
                if pattern and ip:
                    # 支持通配符 *
                    regex = pattern.replace(".", "\\.").replace("*", ".*")
                    try:
                        if re.search(regex, host):
                            return ip
                    except re.error:
                        pass
            elif isinstance(h, str):
                if h in host:
                    return host
        return None

    # ======== 序列化方法 ========

    def to_summary(self) -> dict:
        """生成配置摘要, 包含所有新字段"""
        return {
            # 顶层字段
            "spider": self.spider[:80] if self.spider else "",
            "wallpaper": self.wallpaper[:80] if self.wallpaper else "",
            "logo": self.logo[:80] if self.logo else "",
            "notice": self.notice[:200] if self.notice else "",
            "danmaku": self.danmaku,
            "proxy": self.proxy,
            "flags": self.flags,
            "ads_count": len(self.ads),
            # 列表统计
            "site_count": len(self.sites),
            "live_count": len(self.lives),
            "parse_count": len(self.parses),
            "doh_count": len(self.doh),
            "proxy_count": len(self.proxies),
            "rule_count": len(self.rules),
            "header_count": len(self.headers),
            "host_count": len(self.hosts),
            # 站点详情
            "sites": [{
                "key": s.key,
                "name": s.name,
                "type": s.type,
                "searchable": s.searchable,
                "filterable": s.filterable,
                "hide": s.hide,
                "timeout": s.timeout,
                "changeable": s.changeable,
                "indexs": s.indexs,
                "style": s.style.to_dict() if isinstance(s.style, Style) else {},
            } for s in self.sites],
            # 直播源详情
            "lives": [{
                "name": l.name,
                "type": l.type,
                "url": l.url,
                "epg": l.epg,
                "api": l.api,
                "boot": l.boot,
                "catchup": l.catchup.to_dict() if isinstance(l.catchup, Catchup) else {},
            } for l in self.lives],
            # 解析器详情
            "parses": [{
                "name": p.name,
                "type": p.type,
                "url": p.url[:80],
            } for p in self.parses],
            # DoH
            "doh": [d.to_dict() for d in self.doh],
            # 代理
            "proxies": [p.to_dict() for p in self.proxies],
            # 规则
            "rules": [r.to_dict() for r in self.rules],
            # 响应头
            "headers": [h.to_dict() for h in self.headers],
            # hosts
            "hosts": self.hosts,
        }
