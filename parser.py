"""
解析器引擎 —— VIP 视频解析 / 嗅探解析
支持配置中的 parses 字段, 将需要解析的播放地址转为可直链播放的 URL
增强: 多种 JSON 返回格式 / 正则嗅探 / JSON_V2 / 自定义请求头
"""

import re
import json
import requests
from typing import Optional, Dict, List
from config import Parse


class ParseEngine:
    """解析器引擎"""

    def __init__(self, parses: List[Parse] = None):
        self.parses = parses or []
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        })

    def set_parses(self, parses: List[Parse]):
        self.parses = parses

    def resolve(self, url: str, flag: str = "", parse_flag: int = 1) -> dict:
        """解析播放地址
        parse_flag: 0=直接播放, 1=需要解析
        返回: {url, header, parse}
        """
        # 如果已经是直链视频格式, 直接返回
        if self._is_direct_play(url):
            return {"url": url, "header": {}, "parse": 0}

        # 如果标记为不需要解析
        if parse_flag == 0:
            return {"url": url, "header": {}, "parse": 0}

        # 尝试用解析器解析
        for p in self.parses:
            try:
                result = self._parse_with(p, url)
                if result and result.get("url"):
                    return result
            except Exception as e:
                print(f"[Parser] 解析器 {p.name} 失败: {e}")
                continue

        # 所有解析器都失败, 尝试直接嗅探
        sniff_result = self._sniff_url(url)
        if sniff_result:
            return {"url": sniff_result, "header": {}, "parse": 0}

        # 返回原始 URL
        return {"url": url, "header": {}, "parse": 0}

    def _is_direct_play(self, url: str) -> bool:
        """判断是否可以直接播放的 URL"""
        if not url:
            return False
        lower = url.lower()
        # 常见直链格式
        patterns = ['.m3u8', '.mp4', '.flv', '.ts', '.mkv', '.avi',
                    '.mov', '.webm', '.mpd', '/m3u8', 'stream',
                    'application/x-mpegurl', 'video/mp4']
        return any(p in lower for p in patterns)

    def _parse_with(self, parser: Parse, url: str) -> Optional[dict]:
        """使用单个解析器解析"""
        if parser.type == 0:
            # JSON 接口解析
            return self._parse_json_api(parser, url)
        elif parser.type == 1:
            # WebView 嗅探解析 (调用嗅探器)
            return self._parse_sniff(parser, url)
        elif parser.type == 2:
            # JSON 接口 (返回格式不同)
            return self._parse_json_api(parser, url, v2=True)
        return None

    def _build_parse_url(self, parser_url: str, url: str) -> str:
        """构建解析请求 URL"""
        if "{url}" in parser_url:
            return parser_url.replace("{url}", requests.utils.quote(url, safe=''))
        elif "?" in parser_url:
            return parser_url + "&url=" + requests.utils.quote(url, safe='')
        else:
            return parser_url + "?url=" + requests.utils.quote(url, safe='')

    def _parse_json_api(self, parser: Parse, url: str, v2: bool = False) -> Optional[dict]:
        """JSON 接口解析
        格式: {parser.url}?url={play_url}
        支持多种返回格式:
        - {"url": "..."}
        - {"data": {"url": "..."}}
        - {"data": {"url": "...", "header": {...}}}
        - {"code": 200, "data": {"url": "..."}}
        - {"playUrl": "..."}
        """
        req_url = self._build_parse_url(parser.url, url)

        try:
            resp = self.session.get(req_url, timeout=15)
            resp.encoding = resp.apparent_encoding

            # 先尝试 JSON 解析
            try:
                data = resp.json()
            except json.JSONDecodeError:
                # 如果不是 JSON, 尝试从文本中嗅探媒体 URL
                return self._sniff_text(resp.text)

            # 兼容多种返回格式
            play_url = ""
            header = {}

            # 格式1: {"url": "..."}
            if data.get("url"):
                play_url = data["url"]
                header = data.get("header", {})

            # 格式2: {"data": {"url": "..."}}
            elif isinstance(data.get("data"), dict):
                play_url = data["data"].get("url", "")
                header = data["data"].get("header", {})

            # 格式3: {"data": {"playUrl": "..."}}
            elif isinstance(data.get("data"), dict) and data["data"].get("playUrl"):
                play_url = data["data"]["playUrl"]

            # 格式4: {"playUrl": "..."}
            elif data.get("playUrl"):
                play_url = data["playUrl"]

            # 格式5: 嵌套 data.data
            elif isinstance(data.get("data"), dict):
                inner = data["data"].get("data", {})
                if isinstance(inner, dict):
                    play_url = inner.get("url", "")

            if play_url:
                if isinstance(header, str):
                    try:
                        header = json.loads(header)
                    except Exception:
                        header = {}
                if not isinstance(header, dict):
                    header = {}

                # 检查 URL 是否需要进一步处理
                if self._is_direct_play(play_url):
                    return {"url": play_url, "header": header or {}, "parse": 0}
                else:
                    # 可能还是需要解析的 URL, 嗅探一下
                    sniffed = self._sniff_url(play_url)
                    if sniffed:
                        return {"url": sniffed, "header": header or {}, "parse": 0}
                    return {"url": play_url, "header": header or {}, "parse": 0}

        except requests.Timeout:
            print(f"[Parser] JSON 接口超时: {parser.name}")
        except Exception as e:
            print(f"[Parser] JSON 接口解析失败: {e}")

        return None

    def _parse_sniff(self, parser: Parse, url: str) -> Optional[dict]:
        """嗅探解析 —— 通过模拟浏览器访问页面, 拦截媒体请求
        桌面端使用 requests + 正则匹配替代 WebView
        """
        try:
            req_url = self._build_parse_url(parser.url, url)
            resp = self.session.get(req_url, timeout=15)
            resp.encoding = resp.apparent_encoding

            return self._sniff_text(resp.text)

        except Exception as e:
            print(f"[Parser] 嗅探解析失败: {e}")

        return None

    def _sniff_text(self, text: str) -> Optional[dict]:
        """从文本中嗅探媒体 URL"""
        # 用正则匹配 m3u8 / mp4 / flv 链接
        patterns = [
            r'(https?://[^\s"\']+\.m3u8[^\s"\']*)',
            r'(https?://[^\s"\']+\.mp4[^\s"\']*)',
            r'(https?://[^\s"\']+\.flv[^\s"\']*)',
            r'(https?://[^\s"\']+\.mkv[^\s"\']*)',
            r'(https?://[^\s"\']+\.ts[^\s"\']*)',
            r'(https?://[^\s"\']+stream[^\s"\']*)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                url = match.group(1)
                # 清理 URL 尾部的特殊字符
                url = url.rstrip('\'"\\<>})]')
                return {"url": url, "header": {}, "parse": 0}

        return None

    def _sniff_url(self, url: str) -> Optional[str]:
        """直接访问 URL 并嗅探媒体链接"""
        try:
            resp = self.session.get(url, timeout=15)
            resp.encoding = resp.apparent_encoding
            result = self._sniff_text(resp.text)
            if result:
                return result["url"]
        except Exception:
            pass
        return None

    def get_parse_list(self) -> list:
        """获取解析器列表"""
        return [{"name": p.name, "type": p.type, "url": p.url} for p in self.parses]
