"""
本地代理服务器 —— 为视频流添加自定义请求头
解决 HTML5 播放器无法设置 User-Agent / Referer 的问题
"""

import threading
import urllib.parse
import urllib.request
import io
from http.server import HTTPServer, BaseHTTPRequestHandler


class ProxyHandler(BaseHTTPRequestHandler):
    """代理请求处理器"""

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle(head_only=True)

    def _handle(self, head_only=False):
        try:
            # 解析查询参数
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            target_url = params.get("url", [None])[0]
            if not target_url:
                self.send_error(400, "Missing url parameter")
                return

            user_agent = params.get("ua", [None])[0]
            referer = params.get("ref", [None])[0]

            # 构建请求头
            headers = {}
            if user_agent:
                headers["User-Agent"] = user_agent
            else:
                headers["User-Agent"] = "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36"
            if referer:
                headers["Referer"] = referer

            # 支持 Range 请求 (视频拖动进度条)
            range_header = self.headers.get("Range")
            if range_header:
                headers["Range"] = range_header

            # 发起请求
            req = urllib.request.Request(target_url, headers=headers, method="GET")
            resp = urllib.request.urlopen(req, timeout=30)

            # 返回响应头
            self.send_response(resp.status)
            for key in ["Content-Type", "Content-Length", "Content-Range",
                        "Accept-Ranges", "Cache-Control"]:
                val = resp.headers.get(key)
                if val:
                    self.send_header(key, val)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            if head_only:
                return

            # 流式传输响应体
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

        except Exception as e:
            self.send_error(502, f"Proxy error: {e}")

    def log_message(self, format, *args):
        # 静默日志
        pass


class ProxyServer:
    """代理服务器"""

    def __init__(self, host="127.0.0.1", port=9978):
        self.host = host
        self.port = port
        self._server: HTTPServer = None
        self._thread: threading.Thread = None

    def start(self):
        """在后台线程启动代理服务器"""
        try:
            self._server = HTTPServer((self.host, self.port), ProxyHandler)
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            print(f"[Proxy] 代理服务器已启动: http://{self.host}:{self.port}")
        except OSError:
            # 端口被占用, 尝试下一个端口
            self.port += 1
            self.start()

    def stop(self):
        if self._server:
            self._server.shutdown()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def build_proxy_url(self, target_url: str, ua: str = "", ref: str = "") -> str:
        """构建代理 URL"""
        params = {"url": target_url}
        if ua:
            params["ua"] = ua
        if ref:
            params["ref"] = ref
        query = urllib.parse.urlencode(params)
        return f"{self.base_url}/?{query}"
