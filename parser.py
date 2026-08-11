"""
解析器引擎 —— 完整复刻 FongMi/TV 的解析器类型
支持 5 种解析器类型:
  Type 0: 嗅探        —— WebView 拦截媒体 URL (桌面端用 requests + 正则)
  Type 1: JSON API    —— GET 请求解析器 URL, 从 JSON 提取 url 字段
  Type 2: JSON 扩展   —— 合并所有 type=1 解析器送入 JAR spider
  Type 3: JSON 聚合   —— 合并所有 type=0/1 解析器送入 JAR spider
  Type 4: 超级解析    —— 并行尝试所有 type=0/1 解析器
"""

import re
import json
import concurrent.futures
from typing import Optional, List, Any
from urllib.parse import urljoin, quote

import requests

from config import Parse


# ======== 媒体类型识别 ========

# 直链媒体后缀 -> MIME 类型
MEDIA_MIME_MAP = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".m3u": "application/vnd.apple.mpegurl",
    ".mp4": "video/mp4",
    ".flv": "video/x-flv",
    ".ts": "video/mp2t",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mpd": "application/dash+xml",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}

# 直链路径标识
DIRECT_PATH_MARKERS = (
    "/m3u8", "/mp4", "/flv", "/stream", "/play",
    "application/x-mpegurl", "application/vnd.apple.mpegurl",
    "video/mp4", "video/x-flv",
)

# 媒体后缀列表 (用于嗅探正则)
MEDIA_EXTENSIONS = (
    "m3u8", "mp4", "flv", "mkv", "ts", "mov",
    "webm", "mpd", "m4a", "mp3", "aac",
)

# 嗅探排除模式 (非媒体资源)
SNIFF_EXCLUDE_PATTERNS = (
    r"\.css", r"\.js(?!\d)", r"\.png", r"\.jpe?g", r"\.gif",
    r"\.svg", r"\.ico", r"\.woff2?", r"\.ttf", r"\.webp",
    r"favicon", r"google", r"facebook", r"analytics",
    r"tracking", r"beacon", r"pixel", r"statistic",
)

# 默认请求超时 (秒)
DEFAULT_TIMEOUT = 15

# 默认 User-Agent (模拟 Android 设备)
DEFAULT_UA = (
    "Mozilla/5.0 (Linux; Android 12; Pixel 6) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)


def guess_format(url: str) -> str:
    """根据 URL 猜测媒体 MIME 类型"""
    if not url:
        return ""
    lower = url.lower().split("?")[0].split("#")[0]
    for ext, mime in MEDIA_MIME_MAP.items():
        if lower.endswith(ext):
            return mime
    # 路径标识
    for marker in DIRECT_PATH_MARKERS:
        if marker in lower:
            if "m3u8" in marker or "mpegurl" in marker:
                return "application/vnd.apple.mpegurl"
            if "mp4" in marker:
                return "video/mp4"
            if "flv" in marker:
                return "video/x-flv"
    return ""


def is_direct_play(url: str) -> bool:
    """判断 URL 是否可以直接播放的直链"""
    if not url:
        return False
    lower = url.lower()
    # 检查后缀
    no_query = lower.split("?")[0]
    for ext in MEDIA_MIME_MAP:
        if no_query.endswith(ext):
            return True
    # 检查路径标识
    for marker in DIRECT_PATH_MARKERS:
        if marker in lower:
            return True
    return False


def clean_url(url: str) -> str:
    """清理 URL 尾部的特殊字符"""
    if not url:
        return url
    # 去掉尾部常见的非法字符
    return url.rstrip("'\"\\<>})] \t\r\n")


def normalize_header(header: Any) -> dict:
    """将 header 规范化为 dict"""
    if not header:
        return {}
    if isinstance(header, dict):
        return header
    if isinstance(header, str):
        try:
            parsed = json.loads(header)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


class ParseEngine:
    """解析器引擎 —— 管理 VIP 视频解析 / 嗅探解析

    对应 FongMi/TV 中的 ParseUtil / ParseImpl
    """

    def __init__(self, parses: List[Parse] = None):
        self.parses: List[Parse] = parses or []
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": DEFAULT_UA,
            "Accept": "application/json, text/plain, text/html, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        self._timeout = DEFAULT_TIMEOUT

    # ======== 解析器列表管理 ========

    def set_parses(self, parses: List[Parse]):
        """设置解析器列表"""
        self.parses = parses or []

    def get_parse_list(self) -> list:
        """返回解析器列表 (按优先级排序)"""
        sorted_parses = self._sort_by_priority(self.parses)
        return [{
            "name": p.name,
            "type": p.type,
            "url": p.url,
            "ext": p.ext if hasattr(p, "ext") else {},
        } for p in sorted_parses]

    def _sort_by_priority(self, parses: List[Parse]) -> List[Parse]:
        """按优先级排序解析器
        优先级规则:
          - type 越小优先级越高 (嗅探/JSON优先)
          - 同类型按原始顺序
        """
        return sorted(parses, key=lambda p: (p.type, self.parses.index(p)
                                             if p in self.parses else 0))

    # ======== 主解析入口 ========

    def resolve(self, url: str, flag: str = "", parse_flag: int = 1) -> dict:
        """解析播放地址

        Args:
            url: 需要解析的播放地址
            flag: 播放来源标识 (如线路名)
            parse_flag: 0=直接播放, 1=需要解析

        Returns:
            dict: {url, header, parse, format}
        """
        # 空地址
        if not url:
            return {"url": "", "header": {}, "parse": 0, "format": ""}

        # 如果已经是直链视频格式, 直接返回
        if is_direct_play(url):
            return {
                "url": url,
                "header": {},
                "parse": 0,
                "format": guess_format(url),
            }

        # 如果标记为不需要解析
        if parse_flag == 0:
            return {
                "url": url,
                "header": {},
                "parse": 0,
                "format": guess_format(url),
            }

        # 尝试用解析器解析
        result = self._resolve_with_all(url, flag)
        if result and result.get("url"):
            return result

        # 所有解析器都失败, 尝试直接嗅探
        sniffed = self._sniff_page(url, {}, {})
        if sniffed:
            return sniffed

        # 返回原始 URL
        return {
            "url": url,
            "header": {},
            "parse": 0,
            "format": guess_format(url),
        }

    def _resolve_with_all(self, url: str, flag: str) -> Optional[dict]:
        """遍历所有解析器尝试解析"""
        if not self.parses:
            return None

        # 检查是否有聚合/超级解析器 (type 2/3/4)
        has_agg = any(p.type in (2, 3, 4) for p in self.parses)

        if has_agg:
            # 优先使用聚合解析
            for p in self._sort_by_priority(self.parses):
                if p.type in (2, 3, 4):
                    result = self.resolve_with_type(url, p.type, flag)
                    if result and result.get("url"):
                        return result

        # 逐个尝试嗅探和 JSON 解析器
        for p in self._sort_by_priority(self.parses):
            if p.type in (0, 1):
                result = self._parse_single(p, url, flag)
                if result and result.get("url"):
                    return result

        return None

    def resolve_with_type(self, url: str, parse_type: int,
                          flag: str = "") -> Optional[dict]:
        """按指定类型解析

        Args:
            url: 需要解析的播放地址
            parse_type: 解析器类型 (0/1/2/3/4)
            flag: 播放来源标识

        Returns:
            dict 或 None
        """
        if parse_type == 0:
            # 嗅探解析
            return self._resolve_type0(url, flag)
        elif parse_type == 1:
            # JSON API 解析
            return self._resolve_type1(url, flag)
        elif parse_type == 2:
            # JSON 扩展 (合并 type=1)
            return self._resolve_type2(url, flag)
        elif parse_type == 3:
            # JSON 聚合 (合并 type=0/1)
            return self._resolve_type3(url, flag)
        elif parse_type == 4:
            # 超级解析 (并行)
            return self._resolve_type4(url, flag)
        return None

    # ======== Type 0: 嗅探解析 ========

    def _resolve_type0(self, url: str, flag: str = "") -> Optional[dict]:
        """Type 0: 嗅探解析
        原版使用 WebView 拦截媒体 URL
        桌面端实现: 使用 requests + 正则提取媒体 URL
        支持 User-Agent 伪装和 Referer 设置
        """
        # 找到所有 type=0 的嗅探解析器
        sniff_parses = [p for p in self.parses if p.type == 0]

        if sniff_parses:
            # 使用嗅探解析器
            for p in sniff_parses:
                result = self._parse_sniff(p, url)
                if result and result.get("url"):
                    return result
        else:
            # 没有配置嗅探解析器, 直接嗅探页面
            return self._sniff_page(url, {}, {})

        return None

    def _parse_sniff(self, parser: Parse, url: str) -> Optional[dict]:
        """使用嗅探解析器解析

        parser.url 可以是:
          - 目标页面 URL (直接嗅探该页面)
          - 解析器 URL (将 play_url 作为参数传入)
        """
        try:
            # 构建请求 URL
            if parser.url and self._is_full_url(parser.url):
                # 如果解析器 URL 已经包含占位符或参数
                if "{url}" in parser.url:
                    req_url = parser.url.replace("{url}", quote(url, safe=""))
                elif "?" in parser.url:
                    req_url = parser.url + "&url=" + quote(url, safe="")
                else:
                    req_url = parser.url + "?url=" + quote(url, safe="")
            else:
                # 直接嗅探目标 URL
                req_url = url

            # 从 ext 中获取自定义 header
            ext = parser.ext if hasattr(parser, "ext") and parser.ext else {}
            custom_headers = ext.get("header", {}) if isinstance(ext, dict) else {}
            if isinstance(custom_headers, str):
                custom_headers = normalize_header(custom_headers)

            headers = {
                "User-Agent": custom_headers.get("User-Agent", DEFAULT_UA),
                "Referer": custom_headers.get("Referer", url),
            }
            # 合并其他自定义 header
            for k, v in custom_headers.items():
                if k.lower() not in ("user-agent", "referer"):
                    headers[k] = v

            return self._sniff_page(req_url, headers, custom_headers)

        except Exception as e:
            print(f"[Parser] 嗅探解析器 {parser.name} 失败: {e}")
            return None

    def _sniff_page(self, url: str, headers: dict,
                    custom_headers: dict) -> Optional[dict]:
        """访问页面并嗅探媒体 URL"""
        try:
            req_headers = {
                "User-Agent": headers.get("User-Agent", DEFAULT_UA),
                "Accept": "text/html, application/json, */*",
            }
            if headers.get("Referer"):
                req_headers["Referer"] = headers["Referer"]
            # 合并其他 header
            for k, v in custom_headers.items():
                if k.lower() not in ("user-agent", "referer", "accept"):
                    req_headers[k] = v

            resp = self.session.get(url, headers=req_headers,
                                    timeout=self._timeout, allow_redirects=True)
            resp.encoding = resp.apparent_encoding

            content_type = resp.headers.get("Content-Type", "")

            # 如果响应本身就是媒体流
            if self._is_media_content_type(content_type):
                return {
                    "url": resp.url,
                    "header": req_headers,
                    "parse": 0,
                    "format": guess_format(resp.url) or content_type,
                }

            # 如果是 JSON 响应, 尝试从 JSON 中提取 URL
            if "json" in content_type.lower() or resp.text.strip().startswith("{"):
                json_result = self._extract_from_json(resp.text)
                if json_result and json_result.get("url"):
                    json_result["format"] = guess_format(json_result["url"])
                    return json_result

            # 从 HTML/文本中嗅探媒体 URL
            media_url = self._sniff_media_from_text(resp.text, resp.url)
            if media_url:
                return {
                    "url": media_url,
                    "header": req_headers,
                    "parse": 0,
                    "format": guess_format(media_url),
                }

        except requests.Timeout:
            print(f"[Parser] 嗅探超时: {url}")
        except Exception as e:
            print(f"[Parser] 嗅探失败: {e}")

        return None

    def _sniff_media_from_text(self, text: str,
                               base_url: str = "") -> Optional[str]:
        """从文本中嗅探媒体 URL"""
        if not text:
            return None

        # 构建媒体 URL 正则 (匹配各种媒体后缀)
        ext_pattern = "|".join(MEDIA_EXTENSIONS)
        # 匹配 http(s) URL + 媒体后缀
        url_pattern = (
            r'https?://[^\s"\'<>\\)]+?\.(?:' + ext_pattern + r')'
            r'(?:\?[^\s"\'<>\\)]*)?'
        )

        matches = re.findall(url_pattern, text, re.IGNORECASE)

        # 过滤排除的 URL
        for match in matches:
            clean = clean_url(match)
            if self._is_excluded_url(clean):
                continue
            # 如果是相对路径转绝对路径
            if base_url and not clean.startswith("http"):
                clean = urljoin(base_url, clean)
            return clean

        # 尝试匹配无后缀的流媒体 URL (含 stream/play 等关键词)
        stream_pattern = (
            r'https?://[^\s"\'<>\\)]+?(?:stream|play_url|video_url|/m3u8/|/mp4/)'
            r'[^\s"\'<>\\)]*'
        )
        matches = re.findall(stream_pattern, text, re.IGNORECASE)
        for match in matches:
            clean = clean_url(match)
            if not self._is_excluded_url(clean):
                return clean

        return None

    def _is_excluded_url(self, url: str) -> bool:
        """检查 URL 是否应被排除"""
        lower = url.lower()
        for pattern in SNIFF_EXCLUDE_PATTERNS:
            if re.search(pattern, lower):
                return True
        return False

    def _is_media_content_type(self, content_type: str) -> bool:
        """检查 Content-Type 是否是媒体类型"""
        ct = content_type.lower()
        media_types = (
            "video/", "audio/", "mpegurl", "x-flv", "mp2t",
            "x-matroska", "dash+xml",
        )
        return any(t in ct for t in media_types)

    # ======== Type 1: JSON API 解析 ========

    def _resolve_type1(self, url: str, flag: str = "") -> Optional[dict]:
        """Type 1: JSON API 解析
        GET 请求解析器 URL, 传递 play_url 参数
        从响应 JSON 中提取 url 字段
        支持 header 字段返回请求头
        """
        json_parses = [p for p in self.parses if p.type == 1]

        for p in json_parses:
            result = self._parse_json_api(p, url)
            if result and result.get("url"):
                return result

        return None

    def _parse_json_api(self, parser: Parse, url: str) -> Optional[dict]:
        """JSON 接口解析
        格式: {parser.url}?url={play_url}
        支持多种返回格式:
          - {"url": "..."}
          - {"url": "...", "header": {...}}
          - {"data": {"url": "...", "header": {...}}}
          - {"code": 200, "data": {"url": "..."}}
          - {"playUrl": "..."}
          - {"data": {"playUrl": "..."}}
        """
        req_url = self._build_parse_url(parser.url, url)

        try:
            # 从 ext 中获取自定义请求头
            ext = parser.ext if hasattr(parser, "ext") and parser.ext else {}
            req_headers = {}
            if isinstance(ext, dict):
                req_headers = ext.get("header", {})
                if isinstance(req_headers, str):
                    req_headers = normalize_header(req_headers)

            resp = self.session.get(req_url, headers=req_headers or None,
                                    timeout=self._timeout)
            resp.encoding = resp.apparent_encoding

            # 先尝试 JSON 解析
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError):
                # 如果不是 JSON, 尝试从文本中嗅探媒体 URL
                sniffed = self._sniff_media_from_text(resp.text, resp.url)
                if sniffed:
                    return {
                        "url": sniffed,
                        "header": {},
                        "parse": 0,
                        "format": guess_format(sniffed),
                    }
                return None

            # 从 JSON 中提取播放 URL 和 header
            result = self._extract_from_json_data(data)
            if result and result.get("url"):
                play_url = result["url"]
                header = normalize_header(result.get("header"))

                # 检查 URL 是否需要进一步处理
                if is_direct_play(play_url):
                    return {
                        "url": play_url,
                        "header": header,
                        "parse": 0,
                        "format": guess_format(play_url),
                    }
                else:
                    # 可能还是需要嗅探的 URL
                    sniffed = self._sniff_page(play_url, header, header)
                    if sniffed and sniffed.get("url"):
                        return sniffed
                    # 返回原始 URL
                    return {
                        "url": play_url,
                        "header": header,
                        "parse": 0,
                        "format": guess_format(play_url),
                    }

        except requests.Timeout:
            print(f"[Parser] JSON 接口超时: {parser.name}")
        except Exception as e:
            print(f"[Parser] JSON 接口解析失败 ({parser.name}): {e}")

        return None

    def _extract_from_json(self, text: str) -> Optional[dict]:
        """从 JSON 文本中提取播放 URL"""
        try:
            data = json.loads(text)
            return self._extract_from_json_data(data)
        except (json.JSONDecodeError, ValueError):
            return None

    def _extract_from_json_data(self, data: dict) -> Optional[dict]:
        """从 JSON dict 中提取播放 URL 和 header
        兼容多种返回格式
        """
        if not isinstance(data, dict):
            return None

        play_url = ""
        header = {}

        # 格式1: {"url": "..."}
        if data.get("url"):
            play_url = data["url"]
            header = data.get("header", {})

        # 格式2: {"data": {"url": "..."}}
        elif isinstance(data.get("data"), dict):
            inner = data["data"]
            if inner.get("url"):
                play_url = inner["url"]
                header = inner.get("header", {})
            # 格式3: {"data": {"playUrl": "..."}}
            elif inner.get("playUrl"):
                play_url = inner["playUrl"]
                header = inner.get("header", {})
            # 格式5: 嵌套 data.data
            elif isinstance(inner.get("data"), dict):
                deep = inner["data"]
                if deep.get("url"):
                    play_url = deep["url"]
                    header = deep.get("header", {})

        # 格式4: {"playUrl": "..."}
        elif data.get("playUrl"):
            play_url = data["playUrl"]
            header = data.get("header", {})

        # 格式6: {"code": 200, "url": "..."}
        elif data.get("code") in (200, 1, "200", "1") and data.get("url"):
            play_url = data["url"]
            header = data.get("header", {})

        if play_url:
            return {"url": play_url, "header": header}
        return None

    # ======== Type 2: JSON 扩展解析 ========

    def _resolve_type2(self, url: str, flag: str = "") -> Optional[dict]:
        """Type 2: JSON 扩展
        原版: 将所有 type=1 的解析器信息合并, 送入 JAR spider 处理
        桌面端回退: 遍历所有 type=1 解析器, 返回第一个成功的
        """
        type1_parses = [p for p in self.parses if p.type == 1]

        if not type1_parses:
            return None

        # 尝试通过 JAR spider 处理 (桌面端通常不可用)
        jar_result = self._resolve_via_jar(url, flag, type1_parses)
        if jar_result and jar_result.get("url"):
            return jar_result

        # 桌面端回退: 遍历 type=1 解析器
        for p in type1_parses:
            result = self._parse_json_api(p, url)
            if result and result.get("url"):
                return result

        return None

    # ======== Type 3: JSON 聚合解析 ========

    def _resolve_type3(self, url: str, flag: str = "") -> Optional[dict]:
        """Type 3: JSON 聚合
        原版: 将所有解析器信息(含 type=0/1)合并, 送入 JAR spider 处理
        桌面端回退: 遍历所有解析器, 返回第一个成功的
        """
        all_parses = [p for p in self.parses if p.type in (0, 1)]

        if not all_parses:
            return None

        # 尝试通过 JAR spider 处理
        jar_result = self._resolve_via_jar(url, flag, all_parses)
        if jar_result and jar_result.get("url"):
            return jar_result

        # 桌面端回退: 遍历所有解析器
        for p in self._sort_by_priority(all_parses):
            result = self._parse_single(p, url, flag)
            if result and result.get("url"):
                return result

        return None

    # ======== Type 4: 超级解析 ========

    def _resolve_type4(self, url: str, flag: str = "") -> Optional[dict]:
        """Type 4: 超级解析
        自动并行尝试所有 type=0 和 type=1 的解析器
        使用 ThreadPoolExecutor 并行请求
        返回第一个成功的结果
        """
        candidate_parses = [p for p in self.parses if p.type in (0, 1)]

        if not candidate_parses:
            return None

        # 并行解析
        return self._parallel_resolve(url, flag, candidate_parses)

    def _parallel_resolve(self, url: str, flag: str,
                          parses: List[Parse]) -> Optional[dict]:
        """并行解析, 返回第一个成功的结果"""
        results = {}

        def worker(parser: Parse) -> tuple:
            try:
                result = self._parse_single(parser, url, flag)
                return (parser, result)
            except Exception as e:
                print(f"[Parser] 并行解析 {parser.name} 异常: {e}")
                return (parser, None)

        try:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(parses), 8)
            ) as executor:
                future_map = {
                    executor.submit(worker, p): p for p in parses
                }

                for future in concurrent.futures.as_completed(
                    future_map, timeout=self._timeout
                ):
                    try:
                        parser, result = future.result(timeout=self._timeout)
                        if result and result.get("url"):
                            # 取消其他任务
                            for f in future_map:
                                f.cancel()
                            return result
                    except concurrent.futures.TimeoutError:
                        continue
                    except Exception as e:
                        print(f"[Parser] 并行解析任务异常: {e}")
                        continue

        except concurrent.futures.TimeoutError:
            print("[Parser] 超级解析整体超时")
        except Exception as e:
            print(f"[Parser] 超级解析异常: {e}")

        return None

    # ======== 单解析器调度 ========

    def _parse_single(self, parser: Parse, url: str,
                      flag: str = "") -> Optional[dict]:
        """使用单个解析器解析 (根据 type 分发)"""
        if parser.type == 0:
            return self._parse_sniff(parser, url)
        elif parser.type == 1:
            return self._parse_json_api(parser, url)
        return None

    # ======== JAR spider 桥接 (桌面端通常不可用) ========

    def _resolve_via_jar(self, url: str, flag: str,
                         parses: List[Parse]) -> Optional[dict]:
        """尝试通过 JAR spider 处理
        桌面端无法运行 Dalvik 字节码, 此方法总是返回 None
        """
        # 构建合并的解析器信息 (供未来 JAR spider 使用)
        merged_info = {
            "url": url,
            "flag": flag,
            "parses": [{
                "name": p.name,
                "type": p.type,
                "url": p.url,
                "ext": p.ext if hasattr(p, "ext") else {},
            } for p in parses],
        }

        # 尝试调用 spider 模块的 JarSpider (如果可用)
        try:
            from spider import JarSpider
            from config import Site
            # JAR spider 在桌面端不支持, 直接返回 None
            _ = JarSpider  # noqa: 避免未使用警告
        except ImportError:
            pass

        # 记录合并信息 (调试用)
        _ = merged_info

        return None

    # ======== 工具方法 ========

    def _build_parse_url(self, parser_url: str, url: str) -> str:
        """构建解析请求 URL"""
        if not parser_url:
            return url

        encoded_url = quote(url, safe="")

        if "{url}" in parser_url:
            return parser_url.replace("{url}", encoded_url)
        elif "?" in parser_url:
            return parser_url + "&url=" + encoded_url
        else:
            return parser_url + "?url=" + encoded_url

    def _is_full_url(self, url: str) -> bool:
        """检查是否是完整 URL"""
        return url and (url.startswith("http://") or url.startswith("https://"))

    def _get_session(self, headers: dict = None) -> requests.Session:
        """获取带自定义 header 的 session"""
        if headers:
            session = requests.Session()
            session.headers.update(self.session.headers)
            session.headers.update(headers)
            return session
        return self.session
