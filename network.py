"""
网络功能模块 —— 完整复刻 FongMi/TV 的网络层
包含: DoH 解析 / 代理管理 / CORS 注入 / 广告拦截 /
      Hosts 覆盖 / 嗅探规则 / HTTP 客户端 / 繁简转换
"""

import re
import time
import json
import socket
import threading
from typing import Optional, Dict, List, Tuple, Any
from urllib.parse import urlparse, urljoin

import requests


# 默认 User-Agent (模拟 Android 设备)
DEFAULT_UA = (
    "Mozilla/5.0 (Linux; Android 12; Pixel 6) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)

# DoH 默认缓存 TTL (秒)
DEFAULT_DOH_TTL = 300

# DoH 请求超时 (秒)
DOH_TIMEOUT = 10


# ======== DoH (DNS over HTTPS) ========

class DohResolver:
    """DNS over HTTPS 解析器
    支持 bootstrap IP (直连IP避免DNS劫持)
    支持的 DoH URL 格式:
      - https://dns.google/dns-query
      - https://1.1.1.1/dns-query
    使用 DNS-over-HTTPS JSON API (type=A)
    缓存解析结果 (TTL 300秒)
    """

    # 已知的 DoH bootstrap IP 映射
    BOOTSTRAP_IPS = {
        "dns.google": ["8.8.8.8", "8.8.4.4", "2001:4860:4860::8888"],
        "1.1.1.1": ["1.1.1.1"],
        "cloudflare-dns.com": ["1.1.1.1", "1.0.0.1"],
        "dns.alidns.com": ["223.5.5.5", "223.6.6.6"],
        "doh.pub": ["119.29.29.29"],
    }

    def __init__(self, doh_urls: List[str] = None,
                 bootstrap_ip: str = ""):
        """初始化 DoH 解析器

        Args:
            doh_urls: DoH 服务器 URL 列表
            bootstrap_ip: 直连 IP (避免 DNS 劫持)
        """
        self.doh_urls = doh_urls or []
        self.bootstrap_ip = bootstrap_ip
        self._cache: Dict[str, Tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._ttl = DEFAULT_DOH_TTL

    def resolve(self, host: str) -> Optional[str]:
        """解析主机名为 IP 地址

        Args:
            host: 主机名

        Returns:
            IP 地址字符串, 失败返回 None
        """
        if not host:
            return None

        # 如果已经是 IP 地址, 直接返回
        if self._is_ip(host):
            return host

        # 检查缓存
        with self._lock:
            cached = self._cache.get(host)
            if cached:
                ip, expire_time = cached
                if time.time() < expire_time:
                    return ip
                else:
                    del self._cache[host]

        # 尝试系统 DNS 解析 (作为回退)
        try:
            ip = socket.gethostbyname(host)
            if ip:
                self._cache_result(host, ip)
                return ip
        except socket.gaierror:
            pass
        except Exception:
            pass

        # 尝试 DoH 解析
        for doh_url in self.doh_urls:
            ip = self._resolve_via_doh(doh_url, host)
            if ip:
                self._cache_result(host, ip)
                return ip

        return None

    def _resolve_via_doh(self, doh_url: str, host: str) -> Optional[str]:
        """通过 DoH 服务器解析"""
        try:
            # 解析 DoH 服务器主机名
            parsed = urlparse(doh_url)
            doh_host = parsed.hostname

            # 如果 DoH 服务器主机名需要解析, 使用 bootstrap IP
            session = requests.Session()
            session.headers.update({
                "Accept": "application/dns-json",
                "User-Agent": DEFAULT_UA,
            })

            # 使用 bootstrap IP 直连
            if self.bootstrap_ip:
                # 覆盖 DNS 解析
                doh_ip = self.bootstrap_ip
            elif doh_host in self.BOOTSTRAP_IPS:
                doh_ip = self.BOOTSTRAP_IPS[doh_host][0]
            elif self._is_ip(doh_host):
                doh_ip = doh_host
            else:
                # 递归解析 DoH 服务器 IP (用系统 DNS)
                doh_ip = doh_host

            # 构建 DoH 请求 URL
            if self._is_ip(doh_ip):
                # 使用 IP 直连, 但 Host 头保持域名
                doh_path = parsed.path or "/dns-query"
                if parsed.scheme == "https":
                    request_url = f"https://{doh_ip}{doh_path}"
                else:
                    request_url = f"http://{doh_ip}{doh_path}"
                session.headers.update({"Host": doh_host})
                # 禁用 SSL 证书验证 (因为用 IP 连接)
                session.verify = False
            else:
                request_url = doh_url

            params = {"name": host, "type": "A"}

            resp = session.get(request_url, params=params,
                               timeout=DOH_TIMEOUT)
            if resp.status_code != 200:
                return None

            data = resp.json()
            # DNS-over-HTTPS JSON 格式
            # {"Status": 0, "Answer": [{"name": "...", "type": 1, "data": "1.2.3.4"}]}
            if data.get("Status") != 0:
                return None

            for answer in data.get("Answer", []):
                if answer.get("type") == 1:  # A record
                    ip = answer.get("data", "")
                    if ip and self._is_ip(ip):
                        return ip

        except Exception as e:
            print(f"[DoH] 解析 {host} 失败 ({doh_url}): {e}")

        return None

    def _cache_result(self, host: str, ip: str):
        """缓存解析结果"""
        with self._lock:
            self._cache[host] = (ip, time.time() + self._ttl)

    @staticmethod
    def _is_ip(address: str) -> bool:
        """检查字符串是否是 IP 地址"""
        if not address:
            return False
        try:
            socket.inet_aton(address)
            return True
        except (OSError, ValueError):
            pass
        # IPv6
        try:
            socket.inet_pton(socket.AF_INET6, address)
            return True
        except (OSError, ValueError):
            pass
        return False

    def clear_cache(self):
        """清除缓存"""
        with self._lock:
            self._cache.clear()

    def preload(self, host: str):
        """预加载主机名解析"""
        return self.resolve(host)


# ======== 代理管理 ========

class ProxyManager:
    """代理管理器
    根据 host 正则规则选择代理
    支持 HTTP/HTTPS/SOCKS4/SOCKS5 代理
    """

    def __init__(self, proxy_rules: List[dict] = None,
                 default_proxy: str = ""):
        """初始化代理管理器

        Args:
            proxy_rules: 代理规则列表, 每条规则:
                {
                    "host": "正则表达式匹配域名",
                    "proxy": "http://127.0.0.1:7890",
                    "type": "http"  # http/https/socks4/socks5
                }
            default_proxy: 默认代理 URL
        """
        self.proxy_rules = proxy_rules or []
        self.default_proxy = default_proxy
        self._compiled_rules: List[Tuple[re.Pattern, str]] = []
        self._compile_rules()

    def _compile_rules(self):
        """编译代理规则正则"""
        self._compiled_rules = []
        for rule in self.proxy_rules:
            host_pattern = rule.get("host", "")
            proxy_url = rule.get("proxy", "") or rule.get("url", "")
            if host_pattern and proxy_url:
                try:
                    pattern = re.compile(host_pattern, re.IGNORECASE)
                    self._compiled_rules.append((pattern, proxy_url))
                except re.error:
                    pass

    def set_rules(self, proxy_rules: List[dict]):
        """设置代理规则"""
        self.proxy_rules = proxy_rules or []
        self._compile_rules()

    def get_proxy_for_url(self, url: str) -> Optional[dict]:
        """根据 URL 获取代理配置

        Args:
            url: 请求 URL

        Returns:
            requests 代理 dict 如 {"http": "...", "https": "..."}
            无代理返回 None
        """
        host = self._extract_host(url)
        if not host:
            return None

        # 检查规则匹配
        for pattern, proxy_url in self._compiled_rules:
            if pattern.search(host):
                return self._build_proxy_dict(proxy_url)

        # 使用默认代理
        if self.default_proxy:
            return self._build_proxy_dict(self.default_proxy)

        return None

    def _build_proxy_dict(self, proxy_url: str) -> dict:
        """构建 requests 代理 dict"""
        if not proxy_url:
            return {}

        # 规范化代理 URL
        proxy_url = self._normalize_proxy_url(proxy_url)

        return {
            "http": proxy_url,
            "https": proxy_url,
        }

    def _normalize_proxy_url(self, proxy_url: str) -> str:
        """规范化代理 URL
        支持: http:// / https:// / socks4:// / socks5://
        """
        if not proxy_url:
            return ""

        lower = proxy_url.lower()
        if lower.startswith(("http://", "https://", "socks4://", "socks5://")):
            return proxy_url

        # 无协议前缀, 默认 http
        if "://" not in proxy_url:
            return "http://" + proxy_url

        # socks:// 默认为 socks5
        if lower.startswith("socks://"):
            return "socks5://" + proxy_url[9:]

        return proxy_url

    @staticmethod
    def _extract_host(url: str) -> str:
        """从 URL 中提取主机名"""
        if not url:
            return ""
        parsed = urlparse(url)
        return parsed.hostname or ""

    def apply_to_session(self, session: requests.Session,
                         url: str = "") -> requests.Session:
        """根据 URL 自动设置 session 代理

        Args:
            session: requests Session 对象
            url: 请求 URL (为空则设置默认代理)

        Returns:
            配置好代理的 session
        """
        if url:
            proxy = self.get_proxy_for_url(url)
        elif self.default_proxy:
            proxy = self._build_proxy_dict(self.default_proxy)
        else:
            proxy = None

        if proxy:
            session.proxies.update(proxy)
        else:
            session.proxies.clear()

        return session

    def add_rule(self, host_pattern: str, proxy_url: str):
        """添加代理规则"""
        self.proxy_rules.append({
            "host": host_pattern,
            "proxy": proxy_url,
        })
        self._compile_rules()

    def clear_rules(self):
        """清除所有规则"""
        self.proxy_rules = []
        self._compiled_rules = []


# ======== CORS 注入 ========

class CorsInjector:
    """CORS 注入器
    根据 host 规则在响应中注入自定义标头
    用于解决跨域问题
    """

    def __init__(self, cors_rules: List[dict] = None):
        """初始化 CORS 注入器

        Args:
            cors_rules: CORS 规则列表, 每条规则:
                {
                    "host": "正则表达式匹配域名",
                    "headers": {"Access-Control-Allow-Origin": "*", ...}
                }
        """
        self.cors_rules = cors_rules or []
        self._compiled_rules: List[Tuple[re.Pattern, dict]] = []
        self._compile_rules()

    def _compile_rules(self):
        """编译 CORS 规则"""
        self._compiled_rules = []
        for rule in self.cors_rules:
            host_pattern = rule.get("host", "")
            headers = rule.get("headers", {})
            if host_pattern and headers:
                try:
                    pattern = re.compile(host_pattern, re.IGNORECASE)
                    self._compiled_rules.append((pattern, headers))
                except re.error:
                    pass

    def set_rules(self, cors_rules: List[dict]):
        """设置 CORS 规则"""
        self.cors_rules = cors_rules or []
        self._compile_rules()

    def inject_headers(self, url: str,
                        response_headers: dict = None) -> dict:
        """根据 URL 注入 CORS 标头

        Args:
            url: 请求 URL
            response_headers: 原始响应头

        Returns:
            合并后的响应头 dict
        """
        result = dict(response_headers) if response_headers else {}
        host = self._extract_host(url)

        if not host:
            return result

        for pattern, headers in self._compiled_rules:
            if pattern.search(host):
                result.update(headers)

        return result

    def get_default_cors_headers(self) -> dict:
        """获取默认 CORS 标头"""
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With, Range",
            "Access-Control-Expose-Headers": "Content-Length, Content-Range, Content-Type",
            "Access-Control-Allow-Credentials": "true",
        }

    def add_rule(self, host_pattern: str, headers: dict):
        """添加 CORS 规则"""
        self.cors_rules.append({
            "host": host_pattern,
            "headers": headers,
        })
        self._compile_rules()

    @staticmethod
    def _extract_host(url: str) -> str:
        """从 URL 中提取主机名"""
        if not url:
            return ""
        parsed = urlparse(url)
        return parsed.hostname or ""


# ======== 广告拦截 ========

class AdBlocker:
    """广告拦截器
    ads 黑名单域名列表
    支持 * 通配符匹配
    """

    # 默认广告域名黑名单
    DEFAULT_BLOCK_DOMAINS = [
        "doubleclick.net",
        "googlesyndication.com",
        "googleadservices.com",
        "google-analytics.com",
        "googletagmanager.com",
        "googletagservices.com",
        "adservice.google.com",
        "adnxs.com",
        "advertising.com",
        "pubmatic.com",
        "rubiconproject.com",
        "criteo.com",
        "adsystem.com",
        "adsrvr.org",
        "bing.com/ads",
        "facebook.com/tr",
        "amazon-adsystem.com",
        "scorecardresearch.com",
        "quantserve.com",
        "taboola.com",
        "outbrain.com",
        "disqus.com",
        "admob.com",
        "ads.google.com",
        "iadsdk.apple.com",
        "adcolony.com",
        "applovin.com",
        "chartboost.com",
        "unityads.unity3d.com",
        "vungle.com",
        "ironsrc.com",
        "mopub.com",
        "inmobi.com",
        "flurry.com",
    ]

    def __init__(self, block_domains: List[str] = None,
                 block_patterns: List[str] = None):
        """初始化广告拦截器

        Args:
            block_domains: 拦截域名列表 (支持 * 通配符)
            block_patterns: 拦截 URL 正则模式列表
        """
        self.block_domains: List[str] = block_domains or list(
            self.DEFAULT_BLOCK_DOMAINS
        )
        self.block_patterns: List[str] = block_patterns or []
        self._compiled_patterns: List[re.Pattern] = []
        self._compile_patterns()

    def _compile_patterns(self):
        """编译正则模式"""
        self._compiled_patterns = []
        for pattern in self.block_patterns:
            try:
                self._compiled_patterns.append(
                    re.compile(pattern, re.IGNORECASE)
                )
            except re.error:
                pass

    def set_block_domains(self, domains: List[str]):
        """设置拦截域名列表"""
        self.block_domains = domains or []
        self._compile_patterns()

    def add_block_domain(self, domain: str):
        """添加拦截域名"""
        if domain and domain not in self.block_domains:
            self.block_domains.append(domain)

    def add_block_pattern(self, pattern: str):
        """添加拦截正则模式"""
        if pattern and pattern not in self.block_patterns:
            self.block_patterns.append(pattern)
            try:
                self._compiled_patterns.append(
                    re.compile(pattern, re.IGNORECASE)
                )
            except re.error:
                pass

    def is_ad(self, url: str) -> bool:
        """检查 URL 是否是广告

        Args:
            url: 请求 URL

        Returns:
            True 表示是广告 (应拦截)
        """
        if not url:
            return False

        host = self._extract_host(url)
        if not host:
            return False

        lower_url = url.lower()
        lower_host = host.lower()

        # 检查域名匹配
        for domain in self.block_domains:
            if self._match_domain(lower_host, domain.lower()):
                return True

        # 检查 URL 正则模式
        for pattern in self._compiled_patterns:
            if pattern.search(lower_url):
                return True

        # 检查常见广告路径关键词
        ad_path_keywords = (
            "/ads/", "/ad/", "/banner/", "/popup/",
            "/adserver/", "/adimage/", "/adclick/",
            "/advert/", "/affiliate/", "/sponsor/",
        )
        for keyword in ad_path_keywords:
            if keyword in lower_url:
                return True

        return False

    def _match_domain(self, host: str, pattern: str) -> bool:
        """匹配域名 (支持 * 通配符)

        通配符规则:
          - *.example.com 匹配 sub.example.com, a.b.example.com
            以及 example.com 本身
          - example.* 匹配 example.com, example.cn 等

        Args:
            host: 实际主机名 (小写)
            pattern: 匹配模式 (小写, 支持 *)

        Returns:
            是否匹配
        """
        if not pattern:
            return False

        # 如果模式不含通配符, 直接比较
        if "*" not in pattern:
            # 精确匹配 或 后缀匹配
            return host == pattern or host.endswith("." + pattern)

        # 处理 *. 开头的模式: 匹配任意子域名及基础域名
        if pattern.startswith("*."):
            base_domain = pattern[2:]  # 去掉 *.
            # 匹配基础域名或任意子域名
            return host == base_domain or host.endswith("." + base_domain)

        # 处理 .* 结尾的模式: 匹配任意顶级域名
        if pattern.endswith(".*"):
            base_host = pattern[:-2]  # 去掉 .*
            return host == base_host or host.startswith(base_host + ".")

        # 通用通配符匹配: 将 * 转换为正则
        regex_pattern = re.escape(pattern).replace(r"\*", ".*")
        regex = re.compile("^" + regex_pattern + "$", re.IGNORECASE)
        return bool(regex.match(host))

    @staticmethod
    def _extract_host(url: str) -> str:
        """从 URL 中提取主机名"""
        if not url:
            return ""
        parsed = urlparse(url)
        return parsed.hostname or ""

    def filter_urls(self, urls: List[str]) -> List[str]:
        """过滤 URL 列表, 移除广告 URL"""
        return [u for u in urls if not self.is_ad(u)]

    def get_block_count(self) -> int:
        """获取拦截规则数量"""
        return len(self.block_domains) + len(self.block_patterns)


# ======== Hosts 解析覆盖 ========

class HostsResolver:
    """Hosts 解析覆盖
    hosts 列表, 支持通配符 *
    支持 "127.0.0.1 example.com" 格式
    支持 "*.example.com 127.0.0.1" 通配符
    """

    def __init__(self, hosts_entries: List[str] = None):
        """初始化 Hosts 解析器

        Args:
            hosts_entries: hosts 条目列表, 每条格式:
                "ip hostname" 或 "hostname ip" 或 "*.example.com ip"
        """
        self.hosts: List[Tuple[str, str]] = []  # [(pattern, ip), ...]
        self._compiled: List[Tuple[Optional[re.Pattern], str, str]] = []
        if hosts_entries:
            self.load_entries(hosts_entries)

    def load_entries(self, entries: List[str]):
        """加载 hosts 条目"""
        self.hosts = []
        self._compiled = []
        for entry in entries:
            self.add_entry(entry)

    def add_entry(self, entry: str):
        """添加单条 hosts 条目

        支持格式:
            "127.0.0.1 example.com"
            "example.com 127.0.0.1"
            "*.example.com 127.0.0.1"
        """
        if not entry:
            return

        # 去除注释
        line = entry.split("#")[0].strip()
        if not line:
            return

        parts = line.split()
        if len(parts) < 2:
            return

        # 判断哪个是 IP, 哪个是 hostname
        ip = ""
        hostname = ""

        for part in parts:
            if self._is_ip(part):
                ip = part
            else:
                if not hostname:
                    hostname = part

        if not ip or not hostname:
            return

        self.hosts.append((hostname, ip))

        # 编译通配符模式
        if "*" in hostname:
            regex_pattern = re.escape(hostname).replace(r"\*", ".*")
            pattern = re.compile("^" + regex_pattern + "$", re.IGNORECASE)
            self._compiled.append((pattern, hostname, ip))
        else:
            self._compiled.append((None, hostname.lower(), ip))

    def resolve(self, host: str) -> Optional[str]:
        """解析主机名

        Args:
            host: 主机名

        Returns:
            IP 地址, 未匹配返回 None
        """
        if not host:
            return None

        lower_host = host.lower()

        for pattern, hostname, ip in self._compiled:
            if pattern is not None:
                # 通配符匹配
                if pattern.match(lower_host):
                    return ip
            else:
                # 精确匹配 或 后缀匹配
                if lower_host == hostname:
                    return ip
                if lower_host.endswith("." + hostname):
                    return ip

        return None

    @staticmethod
    def _is_ip(address: str) -> bool:
        """检查字符串是否是 IP 地址"""
        if not address:
            return False
        try:
            socket.inet_aton(address)
            return True
        except (OSError, ValueError):
            pass
        try:
            socket.inet_pton(socket.AF_INET6, address)
            return True
        except (OSError, ValueError):
            pass
        return False

    def get_entries(self) -> List[str]:
        """获取所有 hosts 条目"""
        return [f"{ip} {hostname}" for hostname, ip in self.hosts]

    def clear(self):
        """清除所有条目"""
        self.hosts = []
        self._compiled = []


# ======== 嗅探规则 ========

class SniffRule:
    """嗅探规则
    host: 正则匹配域名
    regex: URL 提取正则列表
    script: 自动执行的脚本 (桌面端简化为空)
    exclude: 排除的 URL 正则列表
    """

    # 默认媒体 URL 正则
    DEFAULT_REGEX = [
        r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.mp4[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.flv[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.mkv[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.ts[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.mov[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.webm[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.mpd[^\s\"'<>]*",
    ]

    # 默认排除模式
    DEFAULT_EXCLUDE = [
        r"\.css",
        r"\.js(?!\d)",
        r"\.png",
        r"\.jpe?g",
        r"\.gif",
        r"\.svg",
        r"\.ico",
        r"\.woff2?",
        r"\.ttf",
        r"\.webp",
        r"favicon",
        r"google",
        r"facebook",
        r"analytics",
        r"tracking",
    ]

    def __init__(self, host: str = "", regex: List[str] = None,
                 script: str = "", exclude: List[str] = None):
        """初始化嗅探规则

        Args:
            host: 域名正则表达式
            regex: URL 提取正则列表
            script: 自动执行脚本 (桌面端简化为空)
            exclude: 排除的 URL 正则列表
        """
        self.host = host
        self.regex = regex if regex is not None else list(self.DEFAULT_REGEX)
        self.script = script
        self.exclude = exclude if exclude is not None else list(
            self.DEFAULT_EXCLUDE
        )

        # 编译正则
        self._host_pattern = None
        self._regex_patterns: List[re.Pattern] = []
        self._exclude_patterns: List[re.Pattern] = []
        self._compile()

    def _compile(self):
        """编译正则表达式"""
        # 编译 host 正则
        if self.host:
            try:
                self._host_pattern = re.compile(self.host, re.IGNORECASE)
            except re.error as e:
                print(f"[SniffRule] host 正则编译失败: {e}")
                self._host_pattern = None

        # 编译 URL 提取正则
        self._regex_patterns = []
        for pattern in self.regex:
            try:
                self._regex_patterns.append(
                    re.compile(pattern, re.IGNORECASE)
                )
            except re.error as e:
                print(f"[SniffRule] regex 编译失败: {e}")

        # 编译排除正则
        self._exclude_patterns = []
        for pattern in self.exclude:
            try:
                self._exclude_patterns.append(
                    re.compile(pattern, re.IGNORECASE)
                )
            except re.error as e:
                print(f"[SniffRule] exclude 编译失败: {e}")

    def should_sniff(self, url: str) -> bool:
        """判断是否应该对该 URL 进行嗅探

        Args:
            url: 请求 URL

        Returns:
            True 表示应该嗅探
        """
        if not url:
            return False

        # 如果没有 host 规则, 默认嗅探所有
        if self._host_pattern is None:
            return True

        host = self._extract_host(url)
        if not host:
            return False

        return bool(self._host_pattern.search(host))

    def extract_urls(self, html_content: str,
                    base_url: str = "") -> List[str]:
        """从 HTML 内容中提取媒体 URL

        Args:
            html_content: HTML/文本内容
            base_url: 基础 URL (用于相对路径转绝对路径)

        Returns:
            提取到的媒体 URL 列表
        """
        if not html_content:
            return []

        extracted = set()

        for pattern in self._regex_patterns:
            matches = pattern.findall(html_content)
            for match in matches:
                # 处理 match 可能是 tuple 的情况
                if isinstance(match, tuple):
                    match = match[0] if match else ""
                if not match:
                    continue

                url = match.strip().rstrip("'\"\\<>})]")

                # 检查是否在排除列表中
                if self._is_excluded(url):
                    continue

                # 相对路径转绝对路径
                if base_url and not url.startswith(("http://", "https://")):
                    url = urljoin(base_url, url)

                if url:
                    extracted.add(url)

        return list(extracted)

    def _is_excluded(self, url: str) -> bool:
        """检查 URL 是否应被排除"""
        lower = url.lower()
        for pattern in self._exclude_patterns:
            if pattern.search(lower):
                return True
        return False

    @staticmethod
    def _extract_host(url: str) -> str:
        """从 URL 中提取主机名"""
        if not url:
            return ""
        parsed = urlparse(url)
        return parsed.hostname or ""

    def to_dict(self) -> dict:
        """序列化为 dict"""
        return {
            "host": self.host,
            "regex": self.regex,
            "script": self.script,
            "exclude": self.exclude,
        }


class SniffRuleManager:
    """嗅探规则管理器 —— 管理多个 SniffRule"""

    def __init__(self, rules: List[dict] = None):
        """初始化

        Args:
            rules: 规则列表, 每条:
                {"host": "...", "regex": [...], "script": "...", "exclude": [...]}
        """
        self.rules: List[SniffRule] = []
        if rules:
            for r in rules:
                self.add_rule(r)

    def add_rule(self, rule: dict):
        """添加嗅探规则"""
        sniff_rule = SniffRule(
            host=rule.get("host", ""),
            regex=rule.get("regex"),
            script=rule.get("script", ""),
            exclude=rule.get("exclude"),
        )
        self.rules.append(sniff_rule)

    def get_rule_for_url(self, url: str) -> Optional[SniffRule]:
        """获取适用于该 URL 的嗅探规则"""
        for rule in self.rules:
            if rule.should_sniff(url):
                return rule
        # 默认规则
        return SniffRule()

    def extract_urls(self, html_content: str,
                     base_url: str = "") -> List[str]:
        """使用匹配的规则提取 URL"""
        rule = self.get_rule_for_url(base_url)
        if rule:
            return rule.extract_urls(html_content, base_url)
        return []

    def clear(self):
        """清除所有规则"""
        self.rules.clear()


# ======== 网络请求封装 ========

class HttpClient:
    """HTTP 客户端 —— 整合 DoH/代理/CORS/广告拦截等所有功能
    自动应用 DoH/代理/CORS/广告拦截
    支持 Cookie 持久化
    """

    def __init__(self, doh_resolver: DohResolver = None,
                 proxy_manager: ProxyManager = None,
                 cors_injector: CorsInjector = None,
                 ad_blocker: AdBlocker = None,
                 hosts_resolver: HostsResolver = None,
                 default_headers: dict = None):
        """初始化 HTTP 客户端

        Args:
            doh_resolver: DoH 解析器
            proxy_manager: 代理管理器
            cors_injector: CORS 注入器
            ad_blocker: 广告拦截器
            hosts_resolver: Hosts 解析器
            default_headers: 默认请求头
        """
        self.doh = doh_resolver
        self.proxy = proxy_manager or ProxyManager()
        self.cors = cors_injector or CorsInjector()
        self.ad_blocker = ad_blocker or AdBlocker()
        self.hosts = hosts_resolver or HostsResolver()

        # 创建持久 session (Cookie 管理)
        self.session = requests.Session()

        # 默认请求头
        self.default_headers = {
            "User-Agent": DEFAULT_UA,
            "Accept": "text/html, application/json, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        if default_headers:
            self.default_headers.update(default_headers)

        self.session.headers.update(self.default_headers)

        # 安装 DNS 劫持适配器
        self._install_dns_adapter()

        # 请求超时
        self.timeout = 15

    def _install_dns_adapter(self):
        """安装 DNS 解析适配器
        将 DoH/Hosts 解析集成到 requests 中
        """
        # 通过 monkey-patch socket.getaddrinfo 实现 DNS 覆盖
        self._original_getaddrinfo = socket.getaddrinfo
        socket.getaddrinfo = self._custom_getaddrinfo

    def _custom_getaddrinfo(self, host, port, family=0, type=0,
                            proto=0, flags=0):
        """自定义 DNS 解析
        优先级: Hosts > DoH > 系统 DNS
        """
        if host:
            # 1. 检查 Hosts 覆盖
            hosts_ip = self.hosts.resolve(host)
            if hosts_ip:
                return self._original_getaddrinfo(
                    hosts_ip, port, family, type, proto, flags
                )

            # 2. 检查 DoH 解析
            if self.doh:
                doh_ip = self.doh.resolve(host)
                if doh_ip:
                    return self._original_getaddrinfo(
                        doh_ip, port, family, type, proto, flags
                    )

        # 3. 回退到系统 DNS
        return self._original_getaddrinfo(
            host, port, family, type, proto, flags
        )

    def request(self, method: str, url: str,
                **kwargs) -> requests.Response:
        """发送 HTTP 请求

        自动应用 DoH/代理/CORS/广告拦截

        Args:
            method: HTTP 方法 (GET/POST/PUT/DELETE/HEAD/OPTIONS)
            url: 请求 URL
            **kwargs: requests.request 的其他参数

        Returns:
            requests.Response 对象
        """
        # 广告拦截
        if self.ad_blocker and self.ad_blocker.is_ad(url):
            # 返回空响应
            fake_resp = requests.Response()
            fake_resp.status_code = 204
            fake_resp._content = b""
            fake_resp.url = url
            fake_resp.headers["X-Blocked"] = "ad-blocker"
            return fake_resp

        # 应用代理
        if self.proxy:
            proxy = self.proxy.get_proxy_for_url(url)
            if proxy:
                kwargs.setdefault("proxies", proxy)
                self.session.proxies.update(proxy)

        # 设置超时
        kwargs.setdefault("timeout", self.timeout)

        # 合并默认 header
        headers = dict(self.default_headers)
        if "headers" in kwargs:
            headers.update(kwargs["headers"])
        kwargs["headers"] = headers

        # 发送请求
        try:
            resp = self.session.request(method, url, **kwargs)

            # CORS 注入
            if self.cors:
                resp.headers = requests.structures.CaseInsensitiveDict(
                    self.cors.inject_headers(url, dict(resp.headers))
                )

            return resp

        except requests.exceptions.SSLError:
            # SSL 错误时尝试禁用验证
            kwargs["verify"] = False
            resp = self.session.request(method, url, **kwargs)
            if self.cors:
                resp.headers = requests.structures.CaseInsensitiveDict(
                    self.cors.inject_headers(url, dict(resp.headers))
                )
            return resp

    def get(self, url: str, **kwargs) -> requests.Response:
        """GET 请求快捷方法"""
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        """POST 请求快捷方法"""
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs) -> requests.Response:
        """PUT 请求快捷方法"""
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs) -> requests.Response:
        """DELETE 请求快捷方法"""
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs) -> requests.Response:
        """HEAD 请求快捷方法"""
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs) -> requests.Response:
        """OPTIONS 请求快捷方法"""
        return self.request("OPTIONS", url, **kwargs)

    def get_json(self, url: str, **kwargs) -> Optional[dict]:
        """GET 请求并返回 JSON"""
        resp = self.get(url, **kwargs)
        try:
            return resp.json()
        except (ValueError, json.JSONDecodeError):
            return None

    def get_text(self, url: str, **kwargs) -> str:
        """GET 请求并返回文本"""
        resp = self.get(url, **kwargs)
        resp.encoding = resp.apparent_encoding
        return resp.text

    def download(self, url: str, file_path: str,
                 chunk_size: int = 8192,
                 headers: dict = None) -> bool:
        """下载文件到本地

        Args:
            url: 下载 URL
            file_path: 本地保存路径
            chunk_size: 分块大小
            headers: 自定义请求头

        Returns:
            是否下载成功
        """
        try:
            resp = self.get(url, headers=headers, stream=True)
            if resp.status_code != 200:
                return False

            with open(file_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
            return True
        except Exception as e:
            print(f"[HttpClient] 下载失败: {e}")
            return False

    def set_user_agent(self, ua: str):
        """设置 User-Agent"""
        self.default_headers["User-Agent"] = ua
        self.session.headers["User-Agent"] = ua

    def set_header(self, key: str, value: str):
        """设置默认请求头"""
        self.default_headers[key] = value
        self.session.headers[key] = value

    def clear_cookies(self):
        """清除所有 Cookie"""
        self.session.cookies.clear()

    def close(self):
        """关闭客户端, 恢复 DNS 解析"""
        try:
            socket.getaddrinfo = self._original_getaddrinfo
        except Exception:
            pass
        self.session.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


# ======== 繁简转换 ========

class SimpleConverter:
    """繁简转换器
    内置常用繁体 -> 简体映射表
    支持 to_simple (繁转简) 和 to_traditional (简转繁)
    """

    # 繁体 -> 简体 字符映射表 (500+ 常用字)
    T2S_MAP = {
        "愛": "爱", "礙": "碍", "闇": "暗", "罷": "罢", "備": "备",
        "貝": "贝", "筆": "笔", "畢": "毕", "邊": "边", "變": "变",
        "標": "标", "錶": "表", "別": "别", "賓": "宾", "補": "补",
        "佈": "布", "參": "参", "倉": "仓", "產": "产", "蟬": "蝉",
        "長": "长", "場": "场", "廠": "厂", "車": "车", "徹": "彻",
        "陳": "陈", "塵": "尘", "稱": "称", "遲": "迟", "衝": "冲",
        "醜": "丑", "處": "处", "觸": "触", "辭": "辞", "從": "从",
        "竄": "窜", "達": "达", "帶": "带", "單": "单", "當": "当",
        "黨": "党", "導": "导", "燈": "灯", "敵": "敌", "點": "点",
        "電": "电", "澱": "淀", "東": "东", "動": "动", "獨": "独",
        "斷": "断", "對": "对", "隊": "队", "噸": "吨", "奪": "夺",
        "墮": "堕", "發": "发", "範": "范", "飛": "飞", "廢": "废",
        "費": "费", "墳": "坟", "奮": "奋", "糞": "粪", "豐": "丰",
        "風": "风", "鳳": "凤", "膚": "肤", "婦": "妇", "復": "复",
        "負": "负", "賦": "赋", "蓋": "盖", "幹": "干", "剛": "刚",
        "崗": "岗", "個": "个", "給": "给", "鞏": "巩", "貢": "贡",
        "溝": "沟", "構": "构", "購": "购", "穀": "谷", "顧": "顾",
        "僱": "雇", "掛": "挂", "關": "关", "觀": "观", "廣": "广",
        "歸": "归", "龜": "龟", "國": "国", "過": "过", "華": "华",
        "畫": "画", "話": "话", "壞": "坏", "歡": "欢", "環": "环",
        "還": "还", "匯": "汇", "會": "会", "護": "护", "滬": "沪",
        "劃": "划", "懷": "怀", "壞": "坏", "歡": "欢", "換": "换",
        "黃": "黄", "匯": "汇", "會": "会", "惠": "惠", "獲": "获",
        "機": "机", "極": "极", "幾": "几", "計": "计", "記": "记",
        "際": "际", "繼": "继", "紀": "纪", "夾": "夹", "價": "价",
        "堅": "坚", "間": "间", "簡": "简", "見": "见", "劍": "剑",
        "漸": "渐", "將": "将", "節": "节", "結": "结", "進": "进",
        "經": "经", "莖": "茎", "驚": "惊", "競": "竞", "舊": "旧",
        "劇": "剧", "據": "据", "懼": "惧", "覺": "觉", "決": "决",
        "絕": "绝", "軍": "军", "開": "开", "凱": "凯", "墾": "垦",
        "懇": "恳", "庫": "库", "況": "况", "誇": "夸", "寬": "宽",
        "礦": "矿", "曠": "旷", "虧": "亏", "來": "来", "賴": "赖",
        "藍": "蓝", "攔": "拦", "欄": "栏", "覽": "览", "懶": "懒",
        "爛": "烂", "勞": "劳", "樂": "乐", "淚": "泪", "類": "类",
        "離": "离", "禮": "礼", "裡": "里", "歷": "历", "勵": "励",
        "聯": "联", "憐": "怜", "連": "连", "鏈": "链", "兩": "两",
        "獵": "猎", "臨": "临", "鄰": "邻", "靈": "灵", "領": "领",
        "劉": "刘", "龍": "龙", "樓": "楼", "陸": "陆", "錄": "录",
        "慮": "虑", "縷": "缕", "掄": "抡", "羅": "罗", "駡": "骂",
        "買": "买", "賣": "卖", "邁": "迈", "麥": "麦", "脈": "脉",
        "蠻": "蛮", "滿": "满", "貓": "猫", "貿": "贸", "門": "门",
        "夢": "梦", "彌": "弥", "祕": "秘", "綿": "绵", "廟": "庙",
        "滅": "灭", "篾": "蔑", "麼": "么", "務": "务", "撓": "挠",
        "鬧": "闹", "擬": "拟", "釀": "酿", "聶": "聂", "寧": "宁",
        "農": "农", "歐": "欧", "盤": "盘", "瘡": "疮", "鵬": "鹏",
        "騙": "骗", "蘋": "苹", "評": "评", "憑": "凭", "齊": "齐",
        "騎": "骑", "啟": "启", "氣": "气", "遷": "迁", "籤": "签",
        "潛": "潜", "淺": "浅", "強": "强", "搶": "抢", "慶": "庆",
        "瓊": "琼", "窮": "穷", "區": "区", "軀": "躯", "權": "权",
        "勸": "劝", "確": "确", "讓": "让", "擾": "扰", "熱": "热",
        "認": "认", "紉": "纫", "榮": "荣", "軟": "软", "銳": "锐",
        "塞": "塞", "賽": "赛", "喪": "丧", "殺": "杀", "紗": "纱",
        "曬": "晒", "山": "山", "閃": "闪", "陝": "陕", "贍": "赡",
        "商": "商", "賞": "赏", "燒": "烧", "紹": "绍", "賒": "赊",
        "設": "设", "紳": "绅", "審": "审", "嬸": "婶", "腎": "肾",
        "聲": "声", "勝": "胜", "聖": "圣", "師": "师", "實": "实",
        "適": "适", "勢": "势", "釋": "释", "飾": "饰", "壽": "寿",
        "書": "书", "術": "术", "樹": "树", "雙": "双", "誰": "谁",
        "順": "顺", "說": "说", "絲": "丝", "鬆": "松", "聳": "耸",
        "訟": "讼", "誦": "诵", "蘇": "苏", "肅": "肃", "雖": "虽",
        "隨": "随", "孫": "孙", "損": "损", "縮": "缩", "鎖": "锁",
        "態": "态", "攤": "摊", "貪": "贪", "癱": "瘫", "歎": "叹",
        "湯": "汤", "討": "讨", "騰": "腾", "謄": "誊", "體": "体",
        "條": "条", "鐵": "铁", "聽": "听", "廳": "厅", "銅": "铜",
        "統": "统", "圖": "图", "塗": "涂", "團": "团", "頹": "颓",
        "脫": "脱", "萬": "万", "網": "网", "衛": "卫", "穩": "稳",
        "問": "问", "烏": "乌", "無": "无", "蕪": "芜", "吳": "吴",
        "誤": "误", "習": "习", "戲": "戏", "細": "细", "蝦": "虾",
        "轄": "辖", "峽": "峡", "險": "险", "顯": "显", "獻": "献",
        "鄉": "乡", "詳": "详", "響": "响", "項": "项", "協": "协",
        "寫": "写", "瀉": "泻", "脅": "胁", "褻": "亵", "興": "兴",
        "選": "选", "旋": "旋", "學": "学", "勳": "勋", "壓": "压",
        "鴉": "鸦", "亞": "亚", "啞": "哑", "閹": "阉", "煙": "烟",
        "鹽": "盐", "嚴": "严", "顏": "颜", "兗": "兖", "巖": "岩",
        "揚": "扬", "陽": "阳", "樣": "样", "瑤": "瑶", "搖": "摇",
        "堯": "尧", "業": "业", "葉": "叶", "爺": "爷", "頁": "页",
        "醫": "医", "義": "义", "藝": "艺", "億": "亿", "議": "议",
        "詣": "诣", "陰": "阴", "蔭": "荫", "音": "音", "應": "应",
        "英": "英", "櫻": "樱", "營": "营", "蠅": "蝇", "贏": "赢",
        "影": "影", "擁": "拥", "踴": "踊", "詠": "咏", "湧": "涌",
        "優": "优", "郵": "邮", "與": "与", "語": "语", "預": "预",
        "馭": "驭", "鴛": "鸳", "淵": "渊", "遠": "远", "願": "愿",
        "閱": "阅", "雲": "云", "運": "运", "醞": "酝", "雜": "杂",
        "災": "灾", "贊": "赞", "髒": "脏", "鑿": "凿", "棗": "枣",
        "早": "早", "澤": "泽", "責": "责", "戰": "战", "張": "张",
        "賬": "账", "趙": "赵", "爭": "争", "徵": "征", "鄭": "郑",
        "證": "证", "織": "织", "職": "职", "執": "执", "質": "质",
        "滯": "滞", "鐘": "钟", "種": "种", "腫": "肿", "眾": "众",
        "鑄": "铸", "專": "专", "莊": "庄", "裝": "装", "準": "准",
        "濁": "浊", "資": "资", "總": "总", "組": "组", "鑽": "钻",
        "纘": "缵", "作": "作", "們": "们", "麼": "么", "個": "个",
        "倆": "俩", "傘": "伞", "禮": "礼", "祁": "祁", "禱": "祷",
        "穩": "稳", "誶": "谇", "謫": "谪", "謐": "谧", "謠": "谣",
        "謳": "讴", "謝": "谢", "謠": "谣", "謹": "谨", "謾": "谩",
        "譽": "誉", "讀": "读", "變": "变", "讓": "让", "讕": "谰",
        "讖": "谶", "讚": "赞", "谷": "谷", "豐": "丰", "豔": "艳",
        "豹": "豹", "貉": "貉", "貊": "貊", "貅": "貅", "貘": "貘",
        "貔": "貔", "貅": "貅", "賦": "赋", "賭": "赌", "贖": "赎",
        "賞": "赏", "賜": "赐", "贊": "赞", "贈": "赠", "贄": "贽",
        "贛": "赣", "趙": "赵", "趕": "赶", "趙": "赵", "趨": "趋",
        "蹌": "跄", "蹺": "跷", "蹕": "跸", "蹣": "蹒", "蹤": "踪",
        "躊": "踌", "躉": "躏", "躑": "踯", "躒": "跞", "躥": "蹿",
        "躪": "躏", "軔": "轫", "軛": "轭", "軸": "轴", "軻": "轲",
        "軾": "轼", "轂": "毂", "較": "较", "輊": "轾", "輕": "轻",
        "輒": "辄", "輓": "挽", "輔": "辅", "輛": "辆", "輦": "辇",
        "輩": "辈", "輝": "辉", "輞": "辋", "輟": "辍", "輥": "辊",
        "輩": "辈", "輪": "轮", "輬": "辌", "輮": "辌", "輯": "辑",
        "輳": "辏", "輸": "输", "輻": "辐", "輾": "辗", "輿": "舆",
        "轀": "辒", "轂": "毂", "轄": "辖", "轅": "辕", "轆": "辘",
        "轉": "转", "轍": "辙", "轎": "轿", "轔": "辚", "轡": "辔",
        "轢": "轹", "轤": "辘", "辭": "辞", "辮": "辫", "辯": "辩",
        "農": "农", "邊": "边", "邐": "逦", "還": "还", "邁": "迈",
        "鄒": "邹", "鄔": "鄄", "鄖": "郧", "鄧": "邓", "鄭": "郑",
        "鄲": "郸", "醞": "酝", "醞": "酝", "醫": "医", "醬": "酱",
        "醱": "酦", "醲": "醲", "醴": "醴", "鈀": "钯", "鈁": "钫",
        "鈃": "钘", "鈄": "钭", "鈈": "钚", "鈉": "钠", "鈍": "钝",
        "鈎": "钩", "鈐": "钤", "鈑": "钣", "鈔": "钞", "鈕": "钮",
        "鈞": "钧", "鈣": "钙", "鈥": "钬", "鈦": "钛", "鈧": "钪",
        "鈮": "铌", "鈰": "铈", "鈳": "钶", "鈴": "铃", "鈷": "钴",
        "鈸": "钹", "鈹": "铍", "鈺": "钰", "鈽": "钚", "鈾": "铀",
        "鈿": "钿", "鉀": "钾", "鉅": "钜", "鉈": "铊", "鉋": "铇",
        "鉍": "铋", "鉑": "铂", "鉕": "钷", "鉗": "钳", "鉚": "铆",
        "鉛": "铅", "鉞": "钺", "鉠": "铇", "鉣": "铆", "鉤": "钳",
        "鉥": "铆", "鉦": "钲", "鉬": "钼", "鉭": "钽", "鉸": "铰",
        "鉺": "铒", "鉻": "铬", "鉼": "铆", "鉽": "铆", "鉾": "铆",
        "銀": "银", "銃": "铳", "銅": "铜", "銍": "铚", "銑": "铣",
        "銖": "铢", "銗": "铘", "銘": "铭", "銚": "铫", "銛": "锖",
        "銜": "衔", "銠": "铑", "銣": "铷", "銥": "铱", "銦": "铟",
        "銨": "铵", "銩": "铥", "銪": "铕", "銫": "铯", "銬": "铐",
        "銱": "铞", "銲": "焊", "銳": "锐", "銴": "铆", "銵": "铆",
        "銷": "销", "銹": "锈", "銻": "锑", "銼": "锉", "銽": "铆",
        "銾": "铆", "銿": "铆", "鋀": "铆", "鋁": "铝", "鋂": "铆",
        "鋃": "锒", "鋄": "铆", "鋅": "锌", "鋆": "铆", "鋇": "钡",
        "鋈": "铆", "鋉": "铆", "鋊": "铆", "鋋": "铤", "鋌": "铤",
        "鋍": "铆", "鋎": "铆", "鋏": "铗", "鋐": "铆", "鋑": "铆",
        "鋒": "锋", "鋓": "铆", "鋔": "铆", "鋕": "铆", "鋖": "铆",
        "鋗": "铆", "鋘": "铆", "鋙": "铆", "鋚": "铆", "鋛": "铆",
        "鋝": "锊", "鋞": "铆", "鋟": "锓", "鋠": "铆", "鋡": "铆",
        "鋢": "铆", "鋣": "铆", "鋤": "锄", "鋥": "锃", "鋦": "锔",
        "鋧": "铆", "鋨": "铆", "鋩": "铓", "鋪": "铺", "鋫": "铆",
        "鋬": "铆", "鋭": "锐", "鋮": "铖", "鋯": "锆", "鋰": "锂",
        "鋱": "铽", "鋲": "铆", "鋳": "铸", "鋴": "铆", "鋵": "铆",
        "鋶": "铆", "鋷": "铆", "鋸": "锯", "鋹": "铆", "鋺": "铆",
        "鋻": "铆", "鋼": "钢", "鋽": "铆", "鋾": "铆", "鋿": "铆",
        "錀": "铆", "錁": "锞", "錂": "铆", "錃": "铆", "錄": "录",
        "錅": "铆", "錆": "锖", "錇": "锫", "錈": "锩", "錉": "铆",
        "錊": "铆", "錋": "铆", "錌": "铆", "錍": "铆", "錎": "铆",
        "錏": "铔", "錐": "锥", "錑": "铆", "錒": "锕", "錓": "铆",
        "錔": "铆", "錕": "锟", "錖": "铆", "錗": "铆", "錘": "锤",
        "錙": "锱", "錚": "铮", "錛": "锛", "錜": "铆", "錝": "铆",
        "錞": "铆", "錟": "锬", "錠": "锭", "錡": "锜", "錢": "钱",
        "錣": "铆", "錤": "铆", "錥": "铆", "錦": "锦", "錧": "铆",
        "錨": "锚", "錩": "钽", "錪": "铆", "錫": "锡", "錬": "铆",
        "錭": "铆", "錮": "锢", "錯": "错", "錰": "铆", "錱": "铆",
        "録": "录", "錳": "锰", "錴": "铆", "錵": "铆", "錶": "表",
        "錷": "铆", "錸": "铼", "錹": "铆", "錺": "铆", "錻": "铆",
        "錼": "镎", "錽": "铆", "錾": "錾", "錿": "铆", "鍀": "锝",
        "鍁": "锨", "鍂": "铆", "鍃": "锪", "鍄": "铆", "鍅": "铆",
        "鍆": "钔", "鍇": "锴", "鍈": "铱", "鍉": "鍉", "鍊": "铆",
        "鍋": "锅", "鍌": "铆", "鍍": "镀", "鍎": "铆", "鍏": "铆",
        "鍐": "锫", "鍑": "铆", "鍒": "铆", "鍓": "铆", "鍔": "锷",
        "鍕": "铆", "鍖": "铆", "鍗": "铆", "鍘": "铡", "鍙": "铆",
        "鍚": "锬", "鍛": "锻", "鍜": "铆", "鍝": "铆", "鍞": "铆",
        "鍟": "铆", "鍠": "锽", "鍡": "铆", "鍢": "铆", "鍣": "铆",
        "鍤": "锸", "鍥": "锲", "鍦": "铆", "鍧": "铆", "鍨": "铆",
        "鍩": "铆", "鍪": "鍪", "鍫": "铆", "鍬": "锹", "鍭": "铆",
        "鍮": "铆", "鍯": "铆", "鍰": "铆", "鍱": "铆", "鍲": "铆",
        "鍳": "铆", "鍴": "铆", "鍵": "键", "鍶": "锶", "鍷": "铆",
        "鍸": "铆", "鍹": "铆", "鍺": "锗", "鍻": "铆", "鍼": "针",
        "鍽": "铆", "鍾": "钟", "鍿": "铆", "鎀": "铆", "鎁": "铆",
        "鎂": "镁", "鎃": "铆", "鎄": "铆", "鎅": "铆", "鎆": "铆",
        "鎇": "锔", "鎈": "铆", "鎉": "铆", "鎊": "镑", "鎋": "铆",
        "鎌": "镰", "鎍": "铆", "鎎": "铆", "鎏": "鎏", "鎐": "铆",
        "鎑": "铆", "鎒": "铆", "鎓": "锩", "鎔": "镕", "鎕": "铆",
        "鎖": "锁", "鎗": "枪", "鎘": "镉", "鎙": "铆", "鎚": "锤",
        "鎛": "铆", "鎜": "铆", "鎝": "铆", "鎞": "铆", "鎟": "铆",
        "鎠": "铆", "鎡": "镃", "鎢": "钨", "鎣": "蓥", "鎤": "铆",
        "鎥": "铆", "鎦": "镏", "鎧": "铠", "鎨": "铆", "鎩": "铩",
        "鎪": "锼", "鎫": "铆", "鎬": "镐", "鎭": "铆", "鎮": "镇",
        "鎯": "铆", "鎰": "镒", "鎱": "铆", "鎲": "铆", "鎳": "镍",
        "鎴": "铆", "鎵": "镓", "鎶": "铆", "鎷": "铆", "鎸": "镌",
        "鎹": "铆", "鎺": "铆", "鎻": "铆", "鎼": "铆", "鎽": "铆",
        "鎾": "铆", "鎿": "镎", "鏀": "铆", "鏁": "铆", "鏂": "铆",
        "鏃": "铆", "鏄": "铆", "鏅": "铆", "鏆": "铆", "鏇": "锩",
        "鏈": "链", "鏉": "铆", "鏊": "鏊", "鏋": "铆", "鏌": "镆",
        "鏍": "铆", "鏎": "铆", "鏏": "铆", "鏐": "铆", "鏑": "镝",
        "鏒": "铆", "鏓": "铆", "鏔": "铆", "鏕": "铆", "鏖": "鏖",
        "鏗": "铿", "鏘": "锵", "鏚": "铆", "鏛": "铆", "鏜": "铴",
        "鏝": "镘", "鏞": "铆", "鏟": "铲", "鏠": "铆", "鏡": "镜",
        "鏢": "镖", "鏣": "铆", "鏤": "镂", "鏥": "铆", "鏦": "铆",
        "鏧": "铆", "鏨": "錾", "鏩": "铆", "鏪": "铆", "鏫": "铆",
        "鏬": "铆", "鏭": "铆", "鏮": "铆", "鏯": "铆", "鏰": "镚",
        "鏱": "铆", "鏲": "铆", "鏳": "铆", "鏴": "铆", "鏵": "铧",
        "鏶": "铆", "鏷": "铆", "鏸": "铆", "鏹": "铆", "鏺": "铆",
        "鏻": "鏻", "鏼": "铆", "鏽": "锈", "鏾": "铆", "鏿": "铆",
        "鐀": "铆", "鐁": "铆", "鐂": "铆", "鐃": "铙", "鐄": "铆",
        "鐅": "铆", "鐆": "铆", "鐇": "铆", "鐈": "铆", "鐉": "铆",
        "鐊": "铆", "鐋": "铆", "鐌": "铆", "鐍": "铆", "鐎": "铆",
        "鐏": "铆", "鐐": "镣", "鐑": "铆", "鐒": "铆", "鐓": "镦",
        "鐔": "镡", "鐕": "铆", "鐖": "铆", "鐗": "铆", "鐘": "钟",
        "鐙": "镫", "鐚": "铆", "鐛": "铆", "鐜": "铆", "鐝": "铆",
        "鐞": "铆", "鐟": "铆", "鐠": "镨", "鐡": "铆", "鐢": "铆",
        "鐣": "铆", "鐤": "铆", "鐥": "铆", "鐦": "锎", "鐧": "锧",
        "鐨": "镄", "鐩": "铆", "鐪": "铆", "鐫": "镌", "鐬": "铆",
        "鐭": "铆", "鐮": "镰", "鐯": "铆", "鐰": "铆", "鐱": "铆",
        "鐲": "镯", "鐳": "镭", "鐴": "铆", "鐵": "铁", "鐶": "锾",
        "鐷": "铆", "鐸": "铎", "鐹": "铆", "鐺": "铛", "鐻": "铆",
        "鐼": "铆", "鐽": "铆", "鐾": "铆", "鐿": "镱", "鑀": "铆",
        "鑁": "铆", "鑂": "铆", "鑃": "铆", "鑄": "铸", "鑅": "铆",
        "鑆": "铆", "鑇": "铆", "鑈": "铆", "鑉": "铆", "鑊": "镬",
        "鑋": "铆", "鑌": "锩", "鑍": "铆", "鑎": "铆", "鑏": "铆",
        "鑐": "铆", "鑑": "铆", "鑒": "铆", "鑓": "铆", "鑔": "铆",
        "鑕": "铆", "鑖": "铆", "鑗": "铆", "鑘": "铆", "鑙": "铆",
        "鑚": "铆", "鑛": "铆", "鑜": "铆", "鑝": "铆", "鑞": "铆",
        "鑟": "铆", "鑠": "铆", "鑡": "铆", "鑢": "铆", "鑣": "铆",
        "鑤": "铆", "鑥": "铆", "鑦": "铆", "鑧": "铆", "鑨": "铆",
        "鑩": "铆", "鑪": "铆", "鑫": "铆", "鑬": "铆", "鑭": "铆",
        "鑮": "铆", "鑯": "铆", "鑰": "铆", "鑱": "铆", "鑲": "铆",
        "鑳": "铆", "鑴": "铆", "鑵": "铆", "鑶": "铆", "鑷": "铆",
        "鑸": "铆", "鑹": "铆", "鑺": "铆", "鑻": "铆", "鑼": "铆",
        "鑽": "铆", "鑾": "铆", "鑿": "铆", "钀": "铆", "钁": "镢",
        "钂": "铆", "鑬": "铆", "鑭": "镧", "鑮": "铆", "鑯": "铆",
        "鑰": "钥", "鑱": "镵", "鑲": "镶", "鑳": "铆", "鑴": "铆",
        "鑵": "铆", "鑶": "铆", "鑷": "镊", "鑸": "铆", "鑹": "镲",
        "鑺": "铆", "鑻": "铆", "鑼": "锣", "鑽": "钻", "鑾": "銮",
        "鑿": "凿", "钀": "铆", "钁": "镢", "長": "长", "門": "门",
        "阜": "阜", "阝": "阝", "隶": "隶", "隹": "隹", "雨": "雨",
        "靑": "青", "非": "非", "面": "面", "革": "革", "韋": "韦",
        "韭": "韭", "音": "音", "頁": "页", "風": "风", "飛": "飞",
        "食": "食", "首": "首", "香": "香", "馬": "马", "骨": "骨",
        "高": "高", "髟": "髟", "鬥": "斗", "鬯": "鬯", "鬲": "鬲",
        "鬼": "鬼", "魚": "鱼", "鳥": "鸟", "鹵": "卤", "鹿": "鹿",
        "麥": "麦", "麻": "麻", "黃": "黄", "黍": "黍", "黑": "黑",
        "黹": "黹", "黽": "黾", "鼎": "鼎", "鼓": "鼓", "鼠": "鼠",
        "鼻": "鼻", "齊": "齐", "齒": "齿", "龍": "龙", "龜": "龟",
        "龠": "龠", "舊": "旧", "鹽": "盐", "麵": "面", "裏": "里",
        "衞": "卫", "瀋": "沈", "嚮": "向", "粧": "妆", "剗": "铲",
        "剙": "创", "勳": "勋", "喫": "吃", "咡": "咡", "啗": "啖",
        "啣": "衔", "噲": "哙", "嚙": "啮", "囌": "苏", "壎": "埙",
        "姪": "侄", "娙": "姈", "嬈": "娆", "嬝": "袅", "嬰": "婴",
        "嶇": "岖", "嶠": "峤", "嶢": "峣", "嶧": "峄", "嶮": "崄",
        "巋": "岿", "嶸": "嵘", "巔": "巅", "巰": "巯", "廁": "厕",
        "廂": "厢", "廄": "厩", "廈": "厦", "廒": "廒", "廡": "庑",
        "廢": "废", "廩": "廪", "廬": "庐", "廳": "厅", "弳": "弪",
        "怱": "匆", "憑": "凭", "懣": "懑", "戇": "戆", "撐": "撑",
        "擊": "击", "擋": "挡", "擏": "擏", "擐": "擐", "擔": "担",
        "據": "据", "擠": "挤", "擰": "拧", "擱": "搁", "擱": "搁",
        "擼": "撸", "擾": "扰", "攄": "摅", "攆": "撵", "攏": "拢",
        "攔": "拦", "攖": "撄", "攙": "搀", "攛": "撺", "攜": "携",
        "攝": "摄", "攢": "攒", "攣": "挛", "攤": "摊", "攪": "搅",
        "攬": "揽", "攭": "攭", "敵": "敌", "敺": "驱", "斃": "毙",
        "斕": "斓", "斬": "斩", "斷": "断", "旂": "旗", "昇": "升",
        "曄": "晔", "曆": "历", "曇": "昙", "朧": "胧", "桿": "杆",
        "梘": "枧", "梔": "栀", "條": "条", "梱": "捆", "梲": "棁",
        "梹": "槟", "梻": "梻", "棖": "枨", "棗": "枣", "棧": "栈",
        "棲": "栖", "棶": "棶", "椏": "桠", "椕": "椕", "椚": "椚",
        "椞": "椞", "椡": "椡", "椣": "椣", "椥": "椥", "椦": "椦",
        "椨": "椨", "椩": "椩", "椫": "椫", "椬": "椬", "椮": "椮",
        "椱": "椱", "椲": "椲", "椳": "椳", "椴": "椴", "椵": "椵",
        "椶": "椶", "椷": "椷", "椸": "椸", "椹": "椹", "椺": "椺",
        "椻": "椻", "椼": "椼", "椽": "椽", "椾": "椾", "椿": "椿",
        "楀": "楀", "楁": "楁", "楄": "楄", "楅": "楅", "楆": "楆",
        "楈": "楈", "楉": "楉", "楋": "楋", "楌": "楌", "楍": "楍",
        "楑": "楑", "楒": "楒", "楓": "枫", "楕": "楕", "楖": "楖",
        "楘": "楘", "楙": "楙", "楛": "楛", "楜": "楜", "楝": "楝",
        "楟": "楟", "楡": "榆", "楢": "楢", "楣": "楣", "楤": "楤",
        "楥": "楥", "楧": "楧", "楨": "桢", "楩": "楩", "楪": "楪",
        "楫": "楫", "楬": "楬", "業": "業", "楮": "楮", "楯": "楯",
        "楰": "楰", "楱": "楱", "楲": "楲", "楳": "楳", "楴": "楴",
        "楶": "楶", "楷": "楷", "楸": "楸", "楹": "楹", "楺": "楺",
        "楻": "楻", "楼": "楼", "楾": "楾", "楿": "楿", "楿": "楿",
        "榀": "榀", "榁": "榁", "榃": "榃", "榅": "榅", "榆": "榆",
        "榇": "榇", "榈": "榈", "榉": "榉", "榊": "榊", "榋": "榋",
        "榌": "榌", "榎": "榎", "榏": "榏", "榐": "榐", "榑": "榑",
        "榒": "榒", "榓": "榓", "榔": "榔", "榕": "榕", "榖": "榖",
        "榗": "榗", "榘": "榘", "榙": "榙", "榚": "榚", "榛": "榛",
        "榜": "榜", "榝": "榝", "榞": "榞", "榟": "榟", "榠": "榠",
        "榡": "榡", "榢": "榢", "榣": "榣", "榤": "榤", "榥": "榥",
        "榦": "榦", "榧": "榧", "榩": "榩", "榪": "杩", "榫": "榫",
        "榭": "榭", "榮": "荣", "榱": "榱", "榲": "榲", "榳": "榳",
        "榴": "榴", "榵": "榵", "榶": "榶", "榷": "榷", "榸": "榸",
        "榹": "榹", "榺": "榺", "榻": "榻", "榼": "榼", "榽": "榽",
        "榾": "榾", "榿": "桤", "槀": "槀", "槂": "槂", "槄": "槄",
        "槅": "槅", "槆": "槆", "槇": "槇", "槈": "槈", "槉": "槉",
        "槊": "槊", "槌": "槌", "槍": "枪", "槎": "槎", "槏": "槏",
        "槐": "槐", "槑": "槑", "槒": "槒", "槓": "杠", "槔": "槔",
        "槕": "槕", "槖": "槖", "槗": "槗", "様": "様", "槙": "槙",
        "槚": "槚", "槜": "槜", "槝": "槝", "槞": "槞", "槟": "槟",
        "槠": "槠", "槡": "槡", "槢": "槢", "槣": "槣", "槤": "槤",
        "槥": "槥", "槦": "槦", "槧": "槧", "槨": "槨", "槩": "槩",
        "槪": "槪", "槫": "槫", "槬": "槬", "槭": "槭", "槮": "槮",
        "槯": "槯", "槰": "槰", "槱": "槱", "槲": "槲", "槳": "桨",
        "槴": "槴", "槵": "槵", "槶": "槶", "槷": "槷", "槸": "槸",
        "槹": "槹", "槺": "槺", "槻": "槻", "槼": "槼", "槽": "槽",
        "槾": "槾", "槿": "槿", "樀": "樀", "樁": "桩", "樂": "樂",
        "樃": "樃", "樄": "樄", "樅": "枞", "樆": "樆", "樇": "樇",
        "樈": "樈", "樉": "樉", "樋": "樋", "樌": "樌", "樍": "樍",
        "樎": "樎", "樏": "樏", "樐": "樐", "樑": "梁", "樒": "樒",
        "樓": "楼", "樔": "樔", "樕": "樕", "樖": "樖", "樗": "樗",
        "樘": "樘", "標": "標", "樚": "樚", "樛": "樛", "樜": "樜",
        "樝": "樝", "樞": "枢", "樠": "樠", "模": "模", "樢": "樢",
        "樣": "樣", "樤": "樤", "樥": "樥", "樦": "樦", "樧": "樧",
        "樨": "樨", "権": "権", "横": "横", "樫": "樫", "樬": "樬",
        "樭": "樭", "樮": "樮", "樯": "樯", "樰": "樰", "樱": "樱",
        "樲": "樲", "樳": "樳", "樴": "樴", "樵": "樵", "樶": "樶",
        "樷": "樷", "樸": "朴", "树": "树", "树": "树", "樺": "桦",
        "樻": "樻", "樼": "樼", "樽": "樽", "樾": "樾", "樿": "樿",
        "橀": "橀", "橁": "橁", "橂": "橂", "橃": "橃", "橅": "橅",
        "橆": "橆", "橇": "橇", "橈": "桡", "橉": "橉", "橊": "橊",
        "橋": "桥", "橌": "橌", "橍": "橍", "橎": "橎", "橏": "橏",
        "橐": "橐", "橑": "橑", "橒": "橒", "橓": "橓", "橔": "橔",
        "橕": "橕", "橖": "橖", "橗": "橗", "橚": "橚", "橛": "橛",
        "橜": "橜", "橝": "橝", "橞": "橞", "機": "機", "橠": "橠",
        "橣": "橣", "橤": "橤", "橥": "橥", "橦": "橦", "橧": "橧",
        "橨": "橨", "橩": "橩", "橪": "橪", "橫": "横", "橬": "橬",
        "橭": "橭", "橮": "橮", "橯": "橯", "橰": "橰", "橱": "橱",
        "橲": "橲", "橳": "橳", "橴": "橴", "橵": "橵", "橶": "橶",
        "橷": "橷", "橸": "橸", "橹": "橹", "橺": "橺", "橻": "橻",
        "橼": "橼", "橾": "橾", "橿": "橿", "檀": "檀", "樾": "樾",
        # 补充常用繁简映射
        "視": "视", "請": "请", "講": "讲", "報": "报", "塊": "块",
        "階": "阶", "題": "题", "頭": "头", "養": "养", "餘": "余",
        "驗": "验", "髮": "发", "聞": "闻", "聰": "聪", "腦": "脑",
        "腸": "肠", "膠": "胶", "膚": "肤", "臉": "脸", "臺": "台",
        "艦": "舰", "艱": "艰", "艷": "艳", "芻": "刍", "週": "周",
        "遺": "遗", "醬": "酱", "霧": "雾", "韓": "韩", "頰": "颊",
        "頸": "颈", "頻": "频", "顆": "颗", "驕": "骄", "驟": "骤",
        "骯": "肮", "魷": "鱿", "魯": "鲁", "鮮": "鲜", "鳴": "鸣",
        "鷗": "鸥", "鷹": "鹰", "紙": "纸", "級": "级", "純": "纯",
        "終": "终", "綠": "绿", "緒": "绪", "線": "线", "縣": "县",
        "縱": "纵", "繞": "绕", "繫": "系", "纓": "缨", "纖": "纤",
        "羆": "罴", "羨": "羡", "翹": "翘", "幣": "币", "幗": "帼",
        "屆": "届", "屍": "尸", "屜": "屉", "屢": "屡", "層": "层",
        "嶺": "岭", "嶽": "岳", "婁": "娄", "媧": "娲", "寵": "宠",
        "妝": "妆", "壩": "坝", "壟": "垄", "壢": "垆", "嫗": "妪",
        "嬪": "嫔", "幘": "帻", "藥": "药", "藹": "蔼", "藺": "蔺",
        "蘆": "芦", "蘇": "苏", "蘭": "兰", "蘿": "萝", "虜": "虏",
        "虯": "虬", "蠟": "蜡", "蠶": "蚕", "衊": "蔑", "褲": "裤",
        "規": "规", "覓": "觅", "覘": "觇", "覿": "觌", "訐": "讦",
        "訌": "讧", "託": "托", "訕": "讪", "訖": "讫", "訛": "讹",
        "訝": "讶", "訢": "诉", "訣": "诀", "訥": "讷", "訩": "讻",
        "註": "注", "詁": "诂", "詆": "诋", "詎": "讵", "詐": "诈",
        "詒": "诒", "詔": "诏", "評": "评", "詖": "诐", "詗": "诇",
        "詘": "诎", "詠": "咏", "詡": "诩", "詢": "询", "詣": "诣",
        "詼": "诙", "詿": "诖", "誄": "诔", "誆": "诓", "誇": "夸",
        "誌": "志", "認": "认", "誑": "诳", "誒": "诶", "誠": "诚",
        "誡": "诫", "誣": "诬", "語": "语", "誥": "诰", "誦": "诵",
        "誨": "诲", "誩": "诤", "誫": "谞", "誮": "诮", "誯": "谞",
        "誱": "谞", "課": "课", "誳": "谔", "誴": "谞", "誵": "谞",
        "誶": "谇", "誷": "谞", "誸": "谞", "誹": "诽", "誺": "谞",
        "誻": "谞", "誼": "谊", "誽": "谞", "誾": "谞", "調": "调",
        "諂": "谄", "諄": "谆", "諅": "谞", "諆": "谞", "諈": "谞",
        "諉": "诿", "諊": "谞", "諌": "谞", "諍": "诤", "諎": "谞",
        "諏": "诹", "諐": "愆", "諑": "诼", "諒": "谅", "諓": "谞",
        "諔": "谞", "諕": "谞", "諗": "谂", "諚": "谞", "諛": "谀",
        "諜": "谍", "諝": "谞", "諞": "谝", "諟": "谛", "諠": "喧",
        "諡": "谥", "諢": "诨", "諣": "诟", "諤": "谔", "諥": "谞",
        "諦": "谛", "諧": "谐", "諨": "谞", "諩": "谞", "諪": "谞",
        "諫": "谏", "諬": "谞", "諭": "谕", "諮": "咨", "諯": "谞",
        "諰": "谞", "諱": "讳", "諲": "谞", "諳": "谙", "諴": "谞",
        "諵": "谞", "諶": "谌", "諷": "讽", "諸": "诸", "諹": "谞",
        "諺": "谚", "諻": "谞", "諼": "谖", "諽": "谞", "諿": "谞",
        "謀": "谋", "謁": "谒", "謂": "谓", "謄": "腾", "謅": "诌",
        "謆": "谞", "謇": "謇", "謈": "谞", "謉": "谞", "謊": "谎",
        "謋": "谞", "謌": "谞", "謍": "谞", "謎": "谜", "謏": "谞",
        "謐": "谧", "謑": "谞", "謒": "谞", "謓": "谞", "謔": "谑",
        "謕": "谞", "謖": "谞", "謗": "谤", "謘": "谞", "謙": "谦",
        "謚": "谥", "謜": "谞", "謝": "谢", "謞": "谞", "謟": "诌",
        "謠": "谣", "謡": "谣", "謢": "谞", "謣": "谞", "謤": "谞",
        "謥": "谞", "謦": "謦", "謧": "谞", "謨": "谟", "謩": "谟",
        "謪": "谪", "謫": "谪", "謬": "谬", "謭": "谫", "謮": "谞",
        "謯": "谯", "謰": "谞", "謱": "谞", "謲": "谞", "謳": "讴",
        "謵": "谞", "謶": "谞", "謷": "謷", "謸": "谞", "謹": "谨",
        "謺": "谞", "謻": "谞", "謼": "谞", "謽": "谞", "謾": "谩",
        "謿": "嘲", "譀": "谞", "譁": "哗", "譂": "谞", "譃": "谞",
        "譄": "谞", "譅": "谞", "譆": "嘻", "譇": "谞", "譈": "谞",
        "譊": "呶", "譋": "谞", "譌": "讹", "譍": "应", "譎": "谲",
        "譏": "讥", "譐": "谞", "譑": "谞", "譒": "谞", "譓": "谞",
        "譔": "撰", "譕": "谞", "譖": "谮", "譗": "谞", "識": "识",
        "譙": "谯", "譚": "谭", "譛": "谞", "譜": "谱", "譝": "谞",
        "譞": "谞", "譟": "噪", "譠": "谞", "譡": "谞", "譢": "谞",
        "譣": "谞", "譤": "谞", "譥": "谞", "譧": "谞", "譨": "谞",
        "譩": "谞", "譪": "谞", "譫": "谵", "譬": "譬", "譭": "毁",
        "譮": "谞", "譯": "译", "議": "议", "譱": "善", "譲": "谞",
        "譳": "谞", "譴": "谴", "譵": "谞", "譶": "谞", "護": "护",
        "譸": "谞", "譹": "谞", "譺": "谞", "譻": "谞", "譼": "谞",
        "譽": "誉", "譾": "浅", "譿": "谞", "讀": "读", "讁": "谪",
        "讂": "谞", "讃": "赞", "讄": "谞", "讅": "谞", "讆": "谞",
        "讇": "谞", "讈": "谞", "讉": "谞", "讋": "詟", "讌": "宴",
        "讍": "谞", "讎": "雠", "讏": "谞", "讐": "谞", "讑": "谞",
        "讒": "谗", "讔": "谞", "讕": "谰", "讖": "谶", "讗": "谞",
        "讘": "谞", "讙": "欢", "讚": "赞", "讛": "谞", "讜": "谠",
        "讝": "谞", "讞": "谞", "讟": "谞", "變": "变", "讓": "让",
    }

    def __init__(self):
        """初始化繁简转换器"""
        self._t2s: Dict[str, str] = dict(self.T2S_MAP)
        # 构建简体 -> 繁体 反向映射
        self._s2t: Dict[str, str] = {}
        for trad, simp in self._t2s.items():
            if simp not in self._s2t:
                self._s2t[simp] = trad

    def to_simple(self, text: str) -> str:
        """繁体转简体

        Args:
            text: 繁体文本

        Returns:
            简体文本
        """
        if not text:
            return text
        return "".join(self._t2s.get(ch, ch) for ch in text)

    def to_traditional(self, text: str) -> str:
        """简体转繁体

        Args:
            text: 简体文本

        Returns:
            繁体文本
        """
        if not text:
            return text
        return "".join(self._s2t.get(ch, ch) for ch in text)

    def get_map_size(self) -> int:
        """获取映射表大小"""
        return len(self._t2s)


# ======== 全局便捷函数 ========

def create_default_http_client() -> HttpClient:
    """创建默认配置的 HTTP 客户端"""
    return HttpClient(
        doh_resolver=DohResolver(),
        proxy_manager=ProxyManager(),
        cors_injector=CorsInjector(),
        ad_blocker=AdBlocker(),
        hosts_resolver=HostsResolver(),
    )


def create_http_client_from_config(config_dict: dict) -> HttpClient:
    """从配置 dict 创建 HTTP 客户端

    Args:
        config_dict: 配置 dict, 包含:
            - doh: List[str] - DoH URL 列表
            - proxy: str - 代理 URL
            - hosts: List[str] - hosts 条目列表
            - proxy_rules: List[dict] - 代理规则

    Returns:
        配置好的 HttpClient
    """
    # DoH
    doh_urls = config_dict.get("doh", [])
    doh_resolver = DohResolver(doh_urls) if doh_urls else DohResolver()

    # 代理
    proxy_url = config_dict.get("proxy", "")
    proxy_rules = config_dict.get("proxy_rules", [])
    proxy_manager = ProxyManager(
        proxy_rules=proxy_rules,
        default_proxy=proxy_url,
    )

    # Hosts
    hosts_entries = config_dict.get("hosts", [])
    hosts_resolver = HostsResolver(hosts_entries)

    return HttpClient(
        doh_resolver=doh_resolver,
        proxy_manager=proxy_manager,
        cors_injector=CorsInjector(),
        ad_blocker=AdBlocker(),
        hosts_resolver=hosts_resolver,
    )
