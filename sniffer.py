"""
嗅探器 —— 通过 WebView 加载页面, 拦截媒体请求 URL
桌面端通过 pywebview 的浏览器拦截网络请求
"""

import re
import time
import threading
from typing import Optional, List, Callable

import requests


class Sniffer:
    """媒体 URL 嗅探器"""

    # 匹配媒体流的正则
    MEDIA_PATTERNS = [
        r'\.m3u8',
        r'\.mp4',
        r'\.flv',
        r'\.ts(?:\?|$)',
        r'/m3u8',
        r'/mp4',
        r'video',
        r'stream',
        r'play_url',
        r'video_url',
    ]

    # 排除的模式
    EXCLUDE_PATTERNS = [
        r'\.css',
        r'\.js',
        r'\.png',
        r'\.jpg',
        r'\.jpeg',
        r'\.gif',
        r'\.svg',
        r'\.ico',
        r'\.woff',
        r'\.ttf',
        r'\.webp',
        r'favicon',
        r'google',
        r'facebook',
        r'analytics',
        r'tracking',
    ]

    def __init__(self):
        self._found_urls: List[str] = []
        self._lock = threading.Lock()
        self._timeout = 15

    def sniff_html(self, url: str, headers: dict = None) -> Optional[str]:
        """通过 HTML 页面嗅探媒体 URL
        1. 获取页面 HTML
        2. 正则匹配媒体链接
        3. 如果找到返回 URL, 否则 None
        """
        self._found_urls.clear()

        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            })
            if headers:
                session.headers.update(headers)

            resp = session.get(url, timeout=self._timeout)
            resp.encoding = resp.apparent_encoding
            html = resp.text

            # 匹配所有 URL
            urls = re.findall(r'https?://[^\s"\'<>\\]+', html)
            return self._filter_media_url(urls)

        except Exception as e:
            print(f"[Sniffer] HTML 嗅探失败: {e}")
            return None

    def sniff_api(self, url: str, headers: dict = None) -> Optional[str]:
        """通过 API 接口嗅探
        某些页面通过 JS 动态加载, 先尝试 API 请求
        """
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": url,
            })
            if headers:
                session.headers.update(headers)

            # 尝试常见的 API 路径
            api_paths = [
                url.rstrip('/') + '/api/player',
                url.rstrip('/') + '/api/url',
                url.rstrip('/') + '/api/play',
            ]

            for api_url in api_paths:
                try:
                    resp = session.get(api_url, timeout=10)
                    if resp.status_code == 200:
                        import json
                        data = resp.json()
                        play_url = data.get('url') or data.get('playUrl') or data.get('data', {}).get('url', '')
                        if play_url and self._is_media_url(play_url):
                            return play_url
                except Exception:
                    continue

        except Exception as e:
            print(f"[Sniffer] API 嗅探失败: {e}")

        return None

    def sniff(self, url: str, headers: dict = None) -> Optional[str]:
        """综合嗅探: HTML + API"""
        # 先尝试 HTML 嗅探
        result = self.sniff_html(url, headers)
        if result:
            return result

        # 再尝试 API 嗅探
        result = self.sniff_api(url, headers)
        if result:
            return result

        return None

    def _filter_media_url(self, urls: List[str]) -> Optional[str]:
        """从 URL 列表中筛选媒体 URL"""
        for url in urls:
            if self._is_media_url(url):
                return url
        return None

    def _is_media_url(self, url: str) -> bool:
        """判断 URL 是否是媒体流"""
        lower = url.lower()

        # 排除非媒体 URL
        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, lower):
                return False

        # 匹配媒体 URL
        for pattern in self.MEDIA_PATTERNS:
            if re.search(pattern, lower):
                return True

        return False

    def sniff_with_webview(self, url: str, callback: Callable[[str], None],
                           timeout: int = 15):
        """通过 pywebview 窗口嗅探 (异步)
        在实际 WebView 中加载页面, 拦截网络请求
        需要在主线程中调用
        """
        try:
            import webview
        except ImportError:
            # 回退到 HTML 嗅探
            result = self.sniff(url)
            if result:
                callback(result)
            return

        self._found_urls.clear()
        self._timeout = timeout

        # 创建隐藏窗口嗅探
        def on_loaded():
            """页面加载完成后执行 JS 拦截"""
            pass

        # 使用 webview 的 evaluate_js 来拦截
        # 这是一个简化实现, 实际 WebView 嗅探需要平台特定的网络拦截
        window = webview.create_window(
            title="Sniffing...",
            url=url,
            hidden=True,
            on_top=False,
        )

        def check_urls():
            """定时检查是否找到媒体 URL"""
            start = time.time()
            while time.time() - start < timeout:
                # 通过 JS 获取 performance entries
                try:
                    entries = window.evaluate_js("""
                        JSON.stringify(
                            performance.getEntriesByType('resource')
                                .map(e => e.name)
                                .filter(u => u.match(/m3u8|mp4|flv|stream/i))
                        )
                    """)
                    if entries:
                        import json
                        urls = json.loads(entries)
                        if urls:
                            callback(urls[0])
                            window.destroy()
                            return
                except Exception:
                    pass
                time.sleep(1)

            # 超时
            window.destroy()

        threading.Thread(target=check_urls, daemon=True).start()
