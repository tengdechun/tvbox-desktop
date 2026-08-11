"""
TVBox Desktop —— 本地 HTTP API 远程控制系统
完整复刻 FongMi/TV 原版的 LOCAL.md 接口规范
应用启动后绑定本地 HTTP 服务器, 端口从 9978 起自动检测至 9998
仅依赖 Python 标准库: http.server / threading / socket / uuid / json / os / urllib / sqlite3
"""

import os
import sys
import json
import time
import socket
import uuid as uuid_mod
import hashlib
import sqlite3
import threading
import zipfile
import shutil
import mimetypes
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple


# ======== 常量 ========

PORT_START = 9978
PORT_END = 9998

# 播放状态码 (与 PlaybackStateCompat 对齐)
STATE_NONE = -1
STATE_BUFFERING = 1
STATE_PAUSED = 2
STATE_PLAYING = 3

# 文件根目录 (用于 /file /upload 等端点)
def _get_root_dir() -> str:
    """获取文件服务根目录"""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    root = os.path.join(base, 'storage')
    os.makedirs(root, exist_ok=True)
    return root


# ======== 设备信息 ========

def _get_mac_address() -> str:
    """获取本机 MAC 地址"""
    mac = uuid_mod.getnode()
    if (mac >> 40) & 1:
        # UUID 随机生成的回退
        return "02:00:00:00:00:00"
    return ':'.join(f'{(mac >> ele) & 0xff:02x}' for ele in range(40, -1, -8))


def _get_lan_ip() -> str:
    """获取局域网 IP 地址"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def _get_device_uuid() -> str:
    """基于机器特征生成设备 UUID"""
    mac = _get_mac_address()
    hostname = socket.gethostname()
    raw = f"{mac}|{hostname}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def _build_device_info(port: int) -> dict:
    """构建设备信息字典"""
    lan_ip = _get_lan_ip()
    mac = _get_mac_address()
    return {
        "uuid": _get_device_uuid(),
        "name": socket.gethostname(),
        "ip": f"{lan_ip}:{port}",
        "type": "desktop",
        "mac": mac,
        "serial": mac.replace(":", ""),
        "eth": mac,
        "wlan": mac,
        "time": int(time.time() * 1000),
    }


# ======== 缓存管理 (SQLite) ========

class CacheStore:
    """基于 SQLite settings 表的键值缓存
    Key 计算规则: "cache_" + (rule 为空 ? "" : rule + "_") + key
    """

    def __init__(self, db_path: str = ""):
        if not db_path:
            db_path = self._default_db_path()
        self._db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _default_db_path(self) -> str:
        if os.name == 'nt':
            base = os.environ.get('APPDATA', os.path.expanduser('~'))
            app_dir = os.path.join(base, 'TVBoxDesktop')
        else:
            app_dir = os.path.expanduser('~/.tvbox-desktop')
        os.makedirs(app_dir, exist_ok=True)
        return os.path.join(app_dir, 'tvbox.db')

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn'):
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()

    def _build_key(self, key: str, rule: str = "") -> str:
        if rule:
            return f"cache_{rule}_{key}"
        return f"cache_{key}"

    def get(self, key: str, rule: str = "") -> str:
        full_key = self._build_key(key, rule)
        conn = self._get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (full_key,)).fetchone()
        return row['value'] if row else ""

    def set(self, key: str, value: str, rule: str = "") -> bool:
        full_key = self._build_key(key, rule)
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (full_key, value))
        conn.commit()
        return True

    def delete(self, key: str, rule: str = "") -> bool:
        full_key = self._build_key(key, rule)
        conn = self._get_conn()
        conn.execute("DELETE FROM settings WHERE key=?", (full_key,))
        conn.commit()
        return True


# ======== Multipart 解析 ========

def parse_multipart(body: bytes, boundary: str) -> List[Dict[str, Any]]:
    """解析 multipart/form-data 请求体
    返回: [{"name": str, "filename": str, "content_type": str, "data": bytes}, ...]
    """
    parts = []
    boundary_bytes = ('--' + boundary).encode('utf-8')
    segments = body.split(boundary_bytes)

    for seg in segments:
        seg = seg.strip(b'\r\n')
        if not seg or seg == b'--':
            continue
        if b'\r\n\r\n' not in seg:
            continue

        header_block, data = seg.split(b'\r\n\r\n', 1)
        if data.endswith(b'\r\n'):
            data = data[:-2]

        headers = {}
        for line in header_block.split(b'\r\n'):
            if b':' in line:
                k, v = line.split(b':', 1)
                headers[k.strip().lower().decode('utf-8', 'ignore')] = v.strip().decode('utf-8', 'ignore')

        disposition = headers.get('content-disposition', '')
        name = ''
        filename = ''
        for part in disposition.split(';'):
            part = part.strip()
            if part.startswith('name='):
                name = part[5:].strip('"')
            elif part.startswith('filename='):
                filename = part[9:].strip('"')

        parts.append({
            'name': name,
            'filename': filename,
            'content_type': headers.get('content-type', ''),
            'data': data,
        })

    return parts


# ======== HTTP 请求处理器 ========

class RemoteHandler(BaseHTTPRequestHandler):
    """本地 HTTP API 请求处理器
    所有端点支持 GET 与 POST, 参数可放在 Query String 或 POST Body 中
    响应默认 text/plain, 成功返回 OK, 失败返回 500 + 错误信息
    """

    # 协议版本 (支持 HTTP/1.1 长连接)
    protocol_version = 'HTTP/1.1'

    @property
    def server_ref(self):
        """获取 RemoteServer 引用"""
        return getattr(self.server, 'remote_server', None)

    @property
    def root_dir(self) -> str:
        """文件服务根目录"""
        srv = self.server_ref
        if srv:
            return srv.root_dir
        return _get_root_dir()

    # ======== 基础工具方法 ========

    def _parse_query(self) -> Tuple[str, dict]:
        """解析 URL 路径和查询参数 (不读取 POST body)"""
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        params = {}
        for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items():
            params[k] = v[0] if len(v) == 1 else v
        return path, params

    def _merge_form_params(self, params: dict, body: bytes) -> dict:
        """将 application/x-www-form-urlencoded 的 POST body 参数合并到 params"""
        content_type = self.headers.get('Content-Type', '')
        if 'application/x-www-form-urlencoded' in content_type and body:
            for k, v in urllib.parse.parse_qs(body.decode('utf-8', 'ignore'),
                                               keep_blank_values=True).items():
                params[k] = v[0] if len(v) == 1 else v
        return params

    def _read_body(self) -> bytes:
        """读取 POST 请求体 (raw bytes)"""
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            return self.rfile.read(length)
        return b''

    def _add_cors_headers(self):
        """添加 CORS 头"""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, HEAD')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Max-Age', '86400')

    def _send_text(self, text: str, status: int = 200, content_type: str = 'text/plain; charset=utf-8'):
        """发送文本响应"""
        body = text.encode('utf-8') if isinstance(text, str) else text
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self._add_cors_headers()
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _send_json(self, data: Any, status: int = 200):
        """发送 JSON 响应"""
        text = json.dumps(data, ensure_ascii=False)
        self._send_text(text, status, 'application/json; charset=utf-8')

    def _send_ok(self):
        """发送成功响应"""
        self._send_text('OK')

    def _send_error_msg(self, msg: str, status: int = 500):
        """发送错误响应"""
        self._send_text(msg, status)

    def _redirect(self, location: str):
        """发送重定向"""
        self.send_response(302)
        self.send_header('Location', location)
        self._add_cors_headers()
        self.end_headers()

    def _safe_path(self, rel_path: str) -> Optional[str]:
        """将相对路径转换为安全的绝对路径, 防止目录穿越"""
        root = os.path.abspath(self.root_dir)
        if not rel_path:
            return root
        # 规范化路径, 防止 ../ 穿越
        abs_path = os.path.abspath(os.path.join(root, rel_path))
        if not abs_path.startswith(root):
            return None
        return abs_path

    def _rel_path(self, abs_path: str) -> str:
        """将绝对路径转换为相对根目录的路径"""
        root = os.path.abspath(self.root_dir)
        rel = os.path.relpath(abs_path, root)
        return rel.replace(os.sep, '/')

    # ======== HTTP 方法入口 ========

    def do_OPTIONS(self):
        """处理 OPTIONS 预检请求"""
        self.send_response(200)
        self._add_cors_headers()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_HEAD(self):
        """处理 HEAD 请求"""
        self.do_GET(head_only=True)

    def do_GET(self, head_only: bool = False):
        """处理 GET 请求"""
        try:
            path, params = self._parse_query()
            self._route(path, params, b'', head_only)
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._send_error_msg(f"Error: {e}")
            except Exception:
                pass

    def do_POST(self):
        """处理 POST 请求"""
        try:
            # 先读取 body (只读一次), 再合并表单参数
            body = self._read_body()
            path, params = self._parse_query()
            params = self._merge_form_params(params, body)
            self._route(path, params, body, False)
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._send_error_msg(f"Error: {e}")
            except Exception:
                pass

    # ======== 路由分发 ========

    def _route(self, path: str, params: dict, body: bytes, head_only: bool):
        """根据路径分发到对应的处理器"""
        # 去除前导斜杠
        clean_path = path.lstrip('/')

        # 根路径 -> 返回服务信息
        if clean_path == '' or clean_path == 'index.html':
            self._handle_index()
            return

        # /action
        if clean_path == 'action' or clean_path.startswith('action?'):
            self._handle_action(params, body)
            return

        # /cache
        if clean_path == 'cache' or clean_path.startswith('cache?'):
            self._handle_cache(params)
            return

        # /media
        if clean_path == 'media' or clean_path.startswith('media?'):
            self._handle_media(head_only)
            return

        # /file/{path}
        if clean_path == 'file' or clean_path.startswith('file/') or clean_path.startswith('file?'):
            self._handle_file(clean_path, head_only)
            return

        # /upload
        if clean_path == 'upload' or clean_path.startswith('upload?'):
            self._handle_upload(params, body)
            return

        # /newFolder
        if clean_path == 'newFolder' or clean_path.startswith('newFolder?'):
            self._handle_new_folder(params)
            return

        # /delFolder
        if clean_path == 'delFolder' or clean_path.startswith('delFolder?'):
            self._handle_del_folder(params)
            return

        # /delFile
        if clean_path == 'delFile' or clean_path.startswith('delFile?'):
            self._handle_del_file(params)
            return

        # /parse
        if clean_path == 'parse' or clean_path.startswith('parse?'):
            self._handle_parse(params)
            return

        # /proxy
        if clean_path == 'proxy' or clean_path.startswith('proxy?'):
            self._handle_proxy(params, body, head_only)
            return

        # /device
        if clean_path == 'device' or clean_path.startswith('device?'):
            self._handle_device()
            return

        # 未知路径
        self._send_error_msg(f"Not Found: {path}", 404)

    # ======== /action 端点 ========

    def _handle_action(self, params: dict, body: bytes):
        """处理 /action 端点 —— 通过 do 参数分派不同动作"""
        srv = self.server_ref
        do = params.get('do', '')

        if do == 'control':
            self._action_control(params, srv)
        elif do == 'danmaku':
            self._action_danmaku(params, srv)
        elif do == 'refresh':
            self._action_refresh(params, srv)
        elif do == 'push':
            self._action_push(params, srv)
        elif do == 'file':
            self._action_file(params, srv)
        elif do == 'search':
            self._action_search(params, srv)
        elif do == 'setting':
            self._action_setting(params, srv)
        elif do == 'cast':
            self._action_cast(params, srv)
        elif do == 'sync':
            self._action_sync(params, body, srv)
        else:
            self._send_error_msg(f"Unknown action: {do}", 400)

    def _action_control(self, params: dict, srv):
        """do=control —— 播放控制 (play/pause/stop/prev/next/repeat/replay)"""
        control_type = params.get('type', '')
        valid_types = {'play', 'pause', 'stop', 'prev', 'next', 'repeat', 'replay'}
        if control_type not in valid_types:
            self._send_error_msg(f"Invalid control type: {control_type}", 400)
            return

        if srv and srv.on_play_control:
            try:
                srv.on_play_control(control_type)
            except Exception as e:
                self._send_error_msg(f"Control callback error: {e}")
                return
        self._send_ok()

    def _action_danmaku(self, params: dict, srv):
        """do=danmaku —— 即时发送弹幕到播放器"""
        text = params.get('text', '')
        if not text:
            self._send_error_msg("Missing text parameter", 400)
            return

        if srv and srv.on_send_danmaku:
            try:
                srv.on_send_danmaku(text)
            except Exception as e:
                self._send_error_msg(f"Danmaku callback error: {e}")
                return
        self._send_ok()

    def _action_refresh(self, params: dict, srv):
        """do=refresh —— 刷新页面/推送字幕/推送弹幕/推送Vod对象"""
        refresh_type = params.get('type', '')
        valid_types = {'live', 'detail', 'player', 'subtitle', 'danmaku', 'vod'}
        if refresh_type not in valid_types:
            self._send_error_msg(f"Invalid refresh type: {refresh_type}", 400)
            return

        # 额外参数
        data = {}
        if refresh_type in ('subtitle', 'danmaku'):
            data['path'] = params.get('path', '')
        elif refresh_type == 'vod':
            json_str = params.get('json', '')
            try:
                data['vod'] = json.loads(json_str) if json_str else {}
            except json.JSONDecodeError:
                data['vod'] = json_str

        if srv and srv.on_refresh:
            try:
                srv.on_refresh(refresh_type, data)
            except Exception as e:
                self._send_error_msg(f"Refresh callback error: {e}")
                return
        self._send_ok()

    def _action_push(self, params: dict, srv):
        """do=push —— 推送URL播放"""
        url = params.get('url', '')
        if not url:
            self._send_error_msg("Missing url parameter", 400)
            return

        if srv and srv.on_push_url:
            try:
                srv.on_push_url(url)
            except Exception as e:
                self._send_error_msg(f"Push callback error: {e}")
                return
        self._send_ok()

    def _action_file(self, params: dict, srv):
        """do=file —— 开启本地文件 (桌面版改为打开文件选择器)
        原版行为:
          .apk -> 触发 APK 安装
          .srt/.ssa/.ass -> 注入字幕
          其他 -> 设置页打开
        桌面版: 通过回调通知前端打开文件选择器
        """
        file_path = params.get('path', '')
        if not file_path:
            self._send_error_msg("Missing path parameter", 400)
            return

        ext = os.path.splitext(file_path)[1].lower()

        if srv and srv.on_refresh:
            try:
                if ext in ('.srt', '.ssa', '.ass'):
                    # 字幕注入
                    srv.on_refresh('subtitle', {'path': file_path})
                elif ext == '.apk':
                    # 桌面版无 APK 安装, 返回提示
                    self._send_text("APK install not supported on desktop")
                    return
                else:
                    # 其他文件 -> 设置页打开
                    srv.on_refresh('file', {'path': file_path})
            except Exception as e:
                self._send_error_msg(f"File action error: {e}")
                return
        self._send_ok()

    def _action_search(self, params: dict, srv):
        """do=search —— 触发关键字搜索"""
        word = params.get('word', '')
        if not word:
            self._send_error_msg("Missing word parameter", 400)
            return

        if srv and srv.on_search:
            try:
                srv.on_search(word)
            except Exception as e:
                self._send_error_msg(f"Search callback error: {e}")
                return
        self._send_ok()

    def _action_setting(self, params: dict, srv):
        """do=setting —— 载入配置URL"""
        text = params.get('text', '')
        name = params.get('name', '')
        if not text:
            self._send_error_msg("Missing text parameter", 400)
            return

        if srv and srv.on_load_config:
            try:
                srv.on_load_config(text)
            except Exception as e:
                self._send_error_msg(f"Load config callback error: {e}")
                return
        self._send_ok()

    def _action_cast(self, params: dict, srv):
        """do=cast —— 投放媒体到远端设备 (桌面版返回不支持)"""
        config = params.get('config', '')
        device = params.get('device', '')
        history = params.get('history', '')

        if srv and srv.on_cast:
            try:
                result = srv.on_cast(config, device, history)
                if result is False:
                    self._send_text("Cast not supported on desktop")
                    return
            except Exception as e:
                self._send_error_msg(f"Cast error: {e}")
                return
        # 桌面版默认不支持投屏
        self._send_text("Cast not supported on desktop")

    def _action_sync(self, params: dict, body: bytes, srv):
        """do=sync —— 多设备间同步观看记录/收藏 (简化实现)
        type: "history" 或 "keep"
        device: 目标设备 JSON
        force: "true" = 先删除后合并
        mode: "0"=双向, "1"=仅接收, "2"=仅发送
        POST Body: targets (JSON 数组), configs (JSON 数组, keep 用)
        """
        sync_type = params.get('type', 'history')
        force = params.get('force', '') == 'true'
        mode = params.get('mode', '0')

        # 解析 POST body
        body_str = body.decode('utf-8', 'ignore') if body else ''
        body_params = urllib.parse.parse_qs(body_str, keep_blank_values=True)
        targets_json = body_params.get('targets', [''])[0]
        configs_json = body_params.get('configs', [''])[0]

        targets = []
        if targets_json:
            try:
                targets = json.loads(targets_json)
            except json.JSONDecodeError:
                pass

        configs = []
        if configs_json:
            try:
                configs = json.loads(configs_json)
            except json.JSONDecodeError:
                pass

        if srv and srv.on_sync:
            try:
                result = srv.on_sync({
                    'type': sync_type,
                    'force': force,
                    'mode': mode,
                    'targets': targets,
                    'configs': configs,
                })
                if isinstance(result, dict):
                    self._send_json(result)
                    return
            except Exception as e:
                self._send_error_msg(f"Sync error: {e}")
                return

        # 简化实现: 返回当前数据
        result = {
            'ok': True,
            'type': sync_type,
            'mode': mode,
            'force': force,
            'received_targets': len(targets),
            'message': 'Sync completed (simplified)',
        }
        self._send_json(result)

    # ======== /cache 端点 ========

    def _handle_cache(self, params: dict):
        """处理 /cache 端点 —— 基于 SQLite settings 表的键值缓存"""
        srv = self.server_ref
        if not srv or not srv.cache_store:
            self._send_error_msg("Cache store not available")
            return

        do = params.get('do', '')
        key = params.get('key', '')
        rule = params.get('rule', '')

        if do == 'get':
            value = srv.cache_store.get(key, rule)
            self._send_text(value)
        elif do == 'set':
            value = params.get('value', '')
            srv.cache_store.set(key, value, rule)
            self._send_ok()
        elif do == 'del':
            srv.cache_store.delete(key, rule)
            self._send_ok()
        else:
            self._send_error_msg(f"Unknown cache action: {do}", 400)

    # ======== /media 端点 ========

    def _handle_media(self, head_only: bool):
        """处理 /media 端点 —— 获取播放状态JSON"""
        srv = self.server_ref
        if not srv:
            self._send_json({})
            return

        status = srv.get_playback_status()
        self._send_json(status)

    # ======== /file/{path} 端点 ========

    def _handle_file(self, clean_path: str, head_only: bool):
        """处理 /file/{path} 端点 —— 浏览目录或下载文件
        支持 Range 请求和 ETag 缓存
        """
        # 提取 path 部分 (/file/ 之后的内容)
        if clean_path.startswith('file/'):
            rel_path = clean_path[5:]
        elif clean_path.startswith('file?'):
            rel_path = ''
        else:
            rel_path = ''

        # 去除查询参数
        if '?' in rel_path:
            rel_path = rel_path.split('?')[0]

        abs_path = self._safe_path(rel_path)
        if abs_path is None:
            self._send_error_msg("Access denied: path traversal detected", 403)
            return

        if not os.path.exists(abs_path):
            self._send_error_msg("Not found", 404)
            return

        if os.path.isdir(abs_path):
            self._serve_directory(abs_path, rel_path)
        else:
            self._serve_file(abs_path, head_only)

    def _serve_directory(self, abs_path: str, rel_path: str):
        """列出目录内容 (JSON 格式)"""
        root = os.path.abspath(self.root_dir)

        # 计算 parent 路径
        if not rel_path or rel_path == '.':
            parent = '.'
        else:
            parent_rel = os.path.relpath(os.path.dirname(abs_path), root).replace(os.sep, '/')
            if parent_rel == '.':
                parent = ''
            else:
                parent = parent_rel

        files = []
        try:
            entries = sorted(os.listdir(abs_path), key=lambda x: (not os.path.isdir(os.path.join(abs_path, x)), x.lower()))
        except PermissionError:
            self._send_error_msg("Permission denied", 403)
            return

        for entry in entries:
            entry_path = os.path.join(abs_path, entry)
            entry_rel = os.path.relpath(entry_path, root).replace(os.sep, '/')
            try:
                mtime = os.path.getmtime(entry_path)
                time_str = datetime.fromtimestamp(mtime).strftime('%Y/%m/%d %H:%M:%S')
            except Exception:
                time_str = ''

            is_dir = os.path.isdir(entry_path)
            files.append({
                'name': entry,
                'path': entry_rel,
                'time': time_str,
                'dir': 1 if is_dir else 0,
            })

        result = {
            'parent': parent,
            'files': files,
        }
        self._send_json(result)

    def _serve_file(self, abs_path: str, head_only: bool):
        """提供文件下载, 支持 Range 请求和 ETag 缓存"""
        try:
            file_size = os.path.getsize(abs_path)
        except OSError as e:
            self._send_error_msg(f"Cannot stat file: {e}")
            return

        # 生成 ETag (基于路径+大小+修改时间)
        try:
            mtime = os.path.getmtime(abs_path)
        except OSError:
            mtime = 0
        etag_source = f"{abs_path}:{file_size}:{mtime}"
        etag = '"' + hashlib.md5(etag_source.encode('utf-8')).hexdigest() + '"'

        # 检查 ETag 缓存
        if_none_match = self.headers.get('If-None-Match', '')
        if if_none_match and if_none_match == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self._add_cors_headers()
            self.end_headers()
            return

        # 解析 Range 请求
        range_header = self.headers.get('Range', '')
        start = 0
        end = file_size - 1
        is_partial = False

        if range_header and range_header.startswith('bytes='):
            is_partial = True
            range_spec = range_header[6:]
            if ',' in range_spec:
                range_spec = range_spec.split(',')[0]
            if '-' in range_spec:
                parts = range_spec.split('-', 1)
                start_str = parts[0].strip()
                end_str = parts[1].strip()
                if start_str:
                    start = int(start_str)
                if end_str:
                    end = int(end_str)
                else:
                    end = file_size - 1
            # 修正范围
            if start > file_size - 1:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{file_size}')
                self._add_cors_headers()
                self.end_headers()
                return
            if end >= file_size:
                end = file_size - 1

        content_length = end - start + 1

        # 猜测 MIME 类型
        mime_type, _ = mimetypes.guess_type(abs_path)
        if not mime_type:
            mime_type = 'application/octet-stream'

        # 发送响应头
        if is_partial:
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
        else:
            self.send_response(200)

        self.send_header('Content-Type', mime_type)
        self.send_header('Content-Length', str(content_length))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('ETag', etag)
        self._add_cors_headers()
        self.end_headers()

        if head_only:
            return

        # 流式传输文件内容
        try:
            with open(abs_path, 'rb') as f:
                if start > 0:
                    f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk_size = min(65536, remaining)
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._send_error_msg(f"File read error: {e}")
            except Exception:
                pass

    # ======== /upload 端点 ========

    def _handle_upload(self, params: dict, body: bytes):
        """处理 /upload 端点 —— 上传文件 (POST multipart), 支持 .zip 自动解压"""
        rel_path = params.get('path', '')
        abs_path = self._safe_path(rel_path)
        if abs_path is None:
            self._send_error_msg("Access denied: path traversal detected", 403)
            return

        # 确保目标目录存在
        os.makedirs(abs_path, exist_ok=True)

        # 解析 multipart/form-data
        content_type = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in content_type:
            self._send_error_msg("Content-Type must be multipart/form-data", 400)
            return

        # 提取 boundary
        boundary = ''
        for part in content_type.split(';'):
            part = part.strip()
            if part.startswith('boundary='):
                boundary = part[9:].strip('"')
                break

        if not boundary:
            self._send_error_msg("Missing multipart boundary", 400)
            return

        parts = parse_multipart(body, boundary)
        if not parts:
            self._send_error_msg("No file parts found", 400)
            return

        uploaded = []
        for part in parts:
            filename = part.get('filename', '')
            if not filename:
                continue

            # 安全处理文件名
            safe_name = os.path.basename(filename)
            if not safe_name:
                continue

            file_path = os.path.join(abs_path, safe_name)

            # 写入文件
            try:
                with open(file_path, 'wb') as f:
                    f.write(part['data'])
            except OSError as e:
                self._send_error_msg(f"Failed to write file {safe_name}: {e}")
                return

            # .zip 自动解压
            if safe_name.lower().endswith('.zip'):
                try:
                    extract_dir = os.path.join(abs_path, safe_name[:-4])
                    os.makedirs(extract_dir, exist_ok=True)
                    with zipfile.ZipFile(file_path, 'r') as zf:
                        zf.extractall(extract_dir)
                    uploaded.append({'name': safe_name, 'path': file_path, 'extracted': True})
                except zipfile.BadZipFile as e:
                    uploaded.append({'name': safe_name, 'path': file_path, 'extracted': False,
                                     'error': f'Bad zip: {e}'})
                except Exception as e:
                    uploaded.append({'name': safe_name, 'path': file_path, 'extracted': False,
                                     'error': str(e)})
            else:
                uploaded.append({'name': safe_name, 'path': file_path, 'extracted': False})

        result = {
            'ok': True,
            'count': len(uploaded),
            'files': uploaded,
        }
        self._send_json(result)

    # ======== /newFolder 端点 ========

    def _handle_new_folder(self, params: dict):
        """处理 /newFolder 端点 —— 创建目录"""
        rel_path = params.get('path', '')
        name = params.get('name', '')

        if not name:
            self._send_error_msg("Missing name parameter", 400)
            return

        parent_abs = self._safe_path(rel_path)
        if parent_abs is None:
            self._send_error_msg("Access denied: path traversal detected", 403)
            return

        os.makedirs(parent_abs, exist_ok=True)

        # 安全处理目录名
        safe_name = os.path.basename(name)
        new_dir = os.path.join(parent_abs, safe_name)

        try:
            os.makedirs(new_dir, exist_ok=True)
            self._send_ok()
        except OSError as e:
            self._send_error_msg(f"Failed to create folder: {e}")

    # ======== /delFolder 端点 ========

    def _handle_del_folder(self, params: dict):
        """处理 /delFolder 端点 —— 删除目录及其所有内容"""
        rel_path = params.get('path', '')
        abs_path = self._safe_path(rel_path)
        if abs_path is None:
            self._send_error_msg("Access denied: path traversal detected", 403)
            return

        if not os.path.exists(abs_path):
            self._send_error_msg("Folder not found", 404)
            return

        if not os.path.isdir(abs_path):
            self._send_error_msg("Path is not a directory", 400)
            return

        # 不允许删除根目录
        if os.path.abspath(abs_path) == os.path.abspath(self.root_dir):
            self._send_error_msg("Cannot delete root directory", 403)
            return

        try:
            shutil.rmtree(abs_path)
            self._send_ok()
        except OSError as e:
            self._send_error_msg(f"Failed to delete folder: {e}")

    # ======== /delFile 端点 ========

    def _handle_del_file(self, params: dict):
        """处理 /delFile 端点 —— 删除文件"""
        rel_path = params.get('path', '')
        abs_path = self._safe_path(rel_path)
        if abs_path is None:
            self._send_error_msg("Access denied: path traversal detected", 403)
            return

        if not os.path.exists(abs_path):
            self._send_error_msg("File not found", 404)
            return

        if os.path.isdir(abs_path):
            self._send_error_msg("Path is a directory, use /delFolder instead", 400)
            return

        try:
            os.remove(abs_path)
            self._send_ok()
        except OSError as e:
            self._send_error_msg(f"Failed to delete file: {e}")

    # ======== /parse 端点 ========

    def _handle_parse(self, params: dict):
        """处理 /parse 端点 —— 解析页面 (HTML 模板渲染)"""
        jxs = params.get('jxs', '')
        url = params.get('url', '')

        if not url:
            self._send_error_msg("Missing url parameter", 400)
            return

        # 渲染 parse.html 模板
        html = self._render_parse_template(jxs, url)
        self._send_text(html, content_type='text/html; charset=utf-8')

    def _render_parse_template(self, jxs: str, url: str) -> str:
        """渲染解析页面 HTML 模板"""
        # 对 jxs 和 url 进行 JSON 安全编码
        jxs_escaped = json.dumps(jxs, ensure_ascii=False)
        url_escaped = json.dumps(url, ensure_ascii=False)

        template = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Parse</title>
    <style>
        body { margin: 0; padding: 0; background: #000; color: #fff; font-family: sans-serif; }
        #player { width: 100vw; height: 100vh; }
        .info { position: fixed; top: 10px; left: 10px; z-index: 999; font-size: 12px; opacity: 0.7; }
    </style>
</head>
<body>
    <div class="info" id="info">Parsing...</div>
    <video id="player" controls autoplay></video>
    <script>
        var jxs = JXS_PLACEHOLDER;
        var targetUrl = URL_PLACEHOLDER;
        var info = document.getElementById('info');
        var player = document.getElementById('player');

        function setInfo(text) { info.textContent = text; }

        // 尝试通过解析器获取真实播放地址
        function tryParse() {
            setInfo('Parsing: ' + targetUrl);
            // 桌面版简化: 直接使用 URL 作为播放源
            if (targetUrl) {
                player.src = targetUrl;
                player.play().catch(function(e) {
                    setInfo('Play failed: ' + e.message);
                });
                setInfo('Playing: ' + targetUrl);
            } else {
                setInfo('No URL provided');
            }
        }

        // 如果有解析器脚本, 动态加载
        if (jxs) {
            setInfo('Loading parser: ' + jxs);
            var script = document.createElement('script');
            script.src = jxs;
            script.onload = function() { tryParse(); };
            script.onerror = function() { tryParse(); };
            document.head.appendChild(script);
        } else {
            tryParse();
        }
    </script>
</body>
</html>"""

        html = template.replace('JXS_PLACEHOLDER', jxs_escaped)
        html = html.replace('URL_PLACEHOLDER', url_escaped)
        return html

    # ======== /proxy 端点 ========

    def _handle_proxy(self, params: dict, body: bytes, head_only: bool):
        """处理 /proxy 端点 —— 爬虫代理转发
        将请求转发至目标 URL, 透传查询参数和请求头
        """
        target_url = params.get('url', '')
        if not target_url:
            self._send_error_msg("Missing url parameter", 400)
            return

        # 构建请求头
        user_agent = params.get('ua', '')
        referer = params.get('ref', '')

        headers = {}
        if user_agent:
            headers['User-Agent'] = user_agent
        else:
            headers['User-Agent'] = 'Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36'
        if referer:
            headers['Referer'] = referer

        # 透传 Range 请求
        range_header = self.headers.get('Range')
        if range_header:
            headers['Range'] = range_header

        # 确定请求方法
        method = self.command if self.command in ('GET', 'POST', 'HEAD') else 'GET'

        try:
            req = urllib.request.Request(target_url, headers=headers, method=method)
            if method == 'POST' and body:
                req.data = body

            resp = urllib.request.urlopen(req, timeout=30)
            resp_status = resp.status
            resp_headers = resp.headers

            # 发送响应头
            self.send_response(resp_status)
            for key in ['Content-Type', 'Content-Length', 'Content-Range',
                        'Accept-Ranges', 'Cache-Control', 'Last-Modified']:
                val = resp_headers.get(key)
                if val:
                    self.send_header(key, val)
            self._add_cors_headers()
            self.end_headers()

            if head_only:
                return

            # 流式传输响应体
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except BrokenPipeError:
                    break

        except urllib.error.HTTPError as e:
            self._send_error_msg(f"Proxy HTTP error: {e.code} {e.reason}", e.code)
        except Exception as e:
            self._send_error_msg(f"Proxy error: {e}", 502)

    # ======== /device 端点 ========

    def _handle_device(self):
        """处理 /device 端点 —— 返回装置信息"""
        srv = self.server_ref
        if not srv:
            self._send_json({})
            return

        device_info = dict(srv.device_info)
        device_info['time'] = int(time.time() * 1000)
        self._send_json(device_info)

    # ======== 根路径 ========

    def _handle_index(self):
        """根路径返回服务信息"""
        srv = self.server_ref
        port = srv.get_port() if srv else 0
        info = {
            'name': 'TVBox Desktop Remote API',
            'version': '5.0',
            'port': port,
            'endpoints': [
                '/action', '/cache', '/media', '/file/{path}',
                '/upload', '/newFolder', '/delFolder', '/delFile',
                '/parse', '/proxy', '/device',
            ],
        }
        self._send_json(info)

    # ======== 日志静默 ========

    def log_message(self, format, *args):
        """静默 HTTP 日志 (可重写为开启)"""
        pass


# ======== RemoteServer 主类 ========

class RemoteServer:
    """本地 HTTP API 远程控制服务器
    绑定 0.0.0.0:{port}, 端口从 9978 起自动检测至 9998
    通过回调函数与主应用通信
    """

    def __init__(self, api=None, port_start: int = PORT_START, port_end: int = PORT_END):
        """初始化远程服务器

        Args:
            api: 主API引用, 用于调用播放控制等
            port_start: 起始端口号
            port_end: 结束端口号
        """
        self.api = api
        self.port_start = port_start
        self.port_end = port_end
        self.port: Optional[int] = None
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._cache: Dict[str, str] = {}  # 内存缓存 (备用)
        self._playback_status: dict = {}
        self._playback_lock = threading.Lock()
        self._device_info: dict = {}
        self.root_dir = _get_root_dir()
        self.cache_store = CacheStore()

        # 回调函数 (由主应用设置)
        self.on_play_control: Optional[Callable[[str], None]] = None
        self.on_push_url: Optional[Callable[[str], None]] = None
        self.on_search: Optional[Callable[[str], None]] = None
        self.on_load_config: Optional[Callable[[str], None]] = None
        self.on_send_danmaku: Optional[Callable[[str], None]] = None
        self.on_refresh: Optional[Callable[[str, dict], None]] = None
        self.on_cast: Optional[Callable[[str, str, str], Any]] = None
        self.on_sync: Optional[Callable[[dict], Any]] = None

        # 如果有 api, 自动绑定回调
        self._bind_api_callbacks()

    def _bind_api_callbacks(self):
        """绑定主 API 的回调方法"""
        if not self.api:
            return

        # 尝试绑定播放控制回调
        if hasattr(self.api, 'remote_play_control'):
            self.on_play_control = self.api.remote_play_control
        if hasattr(self.api, 'remote_push_url'):
            self.on_push_url = self.api.remote_push_url
        if hasattr(self.api, 'remote_search'):
            self.on_search = self.api.remote_search
        if hasattr(self.api, 'remote_load_config'):
            self.on_load_config = self.api.remote_load_config
        if hasattr(self.api, 'remote_send_danmaku'):
            self.on_send_danmaku = self.api.remote_send_danmaku
        if hasattr(self.api, 'remote_refresh'):
            self.on_refresh = self.api.remote_refresh
        if hasattr(self.api, 'remote_cast'):
            self.on_cast = self.api.remote_cast
        if hasattr(self.api, 'remote_sync'):
            self.on_sync = self.api.remote_sync

    @property
    def device_info(self) -> dict:
        """设备信息"""
        if not self._device_info:
            self._device_info = _build_device_info(self.port or self.port_start)
        return self._device_info

    def start(self) -> int:
        """启动服务器, 返回实际绑定的端口号
        从 port_start 开始尝试绑定, 被占用则尝试下一个, 直到 port_end
        """
        for port in range(self.port_start, self.port_end + 1):
            try:
                self._server = ThreadingHTTPServer(('0.0.0.0', port), RemoteHandler)
                self._server.remote_server = self  # type: ignore[attr-defined]
                self._server.daemon_threads = True
                self.port = port

                # 更新设备信息 (含实际端口)
                self._device_info = _build_device_info(port)

                self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
                self._thread.start()

                print(f"[Remote] 远程控制服务已启动: http://0.0.0.0:{port}")
                return port

            except OSError:
                # 端口被占用, 尝试下一个
                continue

        # 所有端口都被占用
        raise RuntimeError(f"无法绑定端口: {self.port_start}-{self.port_end} 均被占用")

    def stop(self):
        """停止服务器"""
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        if self._thread:
            self._thread = None
        self.port = None
        print("[Remote] 远程控制服务已停止")

    def get_url(self) -> str:
        """返回服务器的访问 URL (http://ip:port)"""
        ip = _get_lan_ip()
        port = self.port or self.port_start
        return f"http://{ip}:{port}"

    def get_port(self) -> int:
        """返回实际绑定的端口号"""
        return self.port or self.port_start

    def get_local_url(self) -> str:
        """返回本地访问 URL (http://127.0.0.1:port)"""
        port = self.port or self.port_start
        return f"http://127.0.0.1:{port}"

    def set_playback_status(self, status: dict):
        """更新播放器状态 (由播放器回调)"""
        with self._playback_lock:
            self._playback_status = dict(status)

    def get_playback_status(self) -> dict:
        """获取当前播放状态"""
        with self._playback_lock:
            if not self._playback_status:
                return {}
            return dict(self._playback_status)

    def update_playback(self, **kwargs):
        """部分更新播放状态"""
        with self._playback_lock:
            self._playback_status.update(kwargs)

    def is_running(self) -> bool:
        """服务器是否正在运行"""
        return self._server is not None

    def get_device_info(self) -> dict:
        """获取设备信息"""
        info = dict(self.device_info)
        info['time'] = int(time.time() * 1000)
        return info


# ======== 测试入口 ========

def _test():
    """自测入口 —— 独立运行 RemoteServer 进行测试"""
    server = RemoteServer()

    # 设置测试回调
    server.on_play_control = lambda action: print(f"[Callback] play_control: {action}")
    server.on_push_url = lambda url: print(f"[Callback] push_url: {url}")
    server.on_search = lambda keyword: print(f"[Callback] search: {keyword}")
    server.on_load_config = lambda url: print(f"[Callback] load_config: {url}")
    server.on_send_danmaku = lambda text: print(f"[Callback] send_danmaku: {text}")
    server.on_refresh = lambda rtype, data: print(f"[Callback] refresh: {rtype} -> {data}")

    try:
        port = server.start()
        local_url = server.get_local_url()
        print(f"[Test] 服务地址: {local_url}")
        print(f"[Test] 设备信息: {json.dumps(server.get_device_info(), ensure_ascii=False, indent=2)}")
        print("[Test] 按 Ctrl+C 退出...")

        # 模拟播放状态
        server.set_playback_status({
            'url': 'http://example.com/video.m3u8',
            'state': STATE_PLAYING,
            'speed': 1.0,
            'title': '测试视频',
            'artist': '测试来源',
            'artwork': '',
            'duration': 3600000,
            'position': 60000,
        })

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[Test] 正在停止...")
    finally:
        server.stop()


if __name__ == '__main__':
    _test()
