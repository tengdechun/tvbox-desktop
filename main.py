"""
TVBox Desktop v5.0 —— 主入口
pywebview 窗口 + Python API 桥接 + 系统托盘 + 多线程下载
兼容 PyInstaller 打包
"""

import os
import sys
import json
import time
import clipboard
import threading
import subprocess
import webview
import requests

# 兼容 PyInstaller 打包
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, BASE_DIR)

from config import Config
from spider import SpiderManager
from live import LiveParser
from proxy import ProxyServer
from database import Database
from parser import ParseEngine
from epg import EpgParser
from sniffer import Sniffer
from downloader import DownloadManager
from tray import SystemTray, AutoStartManager


class Api:
    """前端可调用的 Python API"""

    def __init__(self):
        self.config = Config()
        self.spider_mgr = SpiderManager()
        self.live_parser = LiveParser()
        self.proxy = ProxyServer()
        self.db = Database()
        self.parse_engine = ParseEngine()
        self.epg = EpgParser()
        self.sniffer = Sniffer()
        self.download_mgr = DownloadManager(self.db, max_concurrent=3)
        self._proxy_started = False
        self._current_site_key = None
        self._current_filters = {}
        self._window = None
        self._tray = None
        self._is_minimized_to_tray = False

        # 注册下载进度回调 -> 通知前端
        self.download_mgr.add_progress_callback(self._on_download_progress)

    def set_window(self, window):
        self._window = window

    def _on_download_progress(self, task):
        """下载进度回调 -> 通过 JS 通知前端"""
        if self._window:
            try:
                error_str = task.error.replace('"', '\\"').replace('\n', ' ')
                progress_val = round(task.downloaded / task.total_size * 100, 1) if task.total_size > 0 else 0
                js_code = (
                    'if (window.App && App.onDownloadProgress) {'
                    f'App.onDownloadProgress({{'
                    f'id: {task.task_id},'
                    f'status: "{task.status}",'
                    f'downloaded: {task.downloaded},'
                    f'total_size: {task.total_size},'
                    f'speed: {round(task.speed, 1)},'
                    f'eta: {round(task.eta, 1)},'
                    f'progress: {progress_val},'
                    f'error: "{error_str}"'
                    '});}'
                )
                self._window.evaluate_js(js_code)
            except Exception:
                pass

    # ======== 配置管理 ========

    def load_config(self, url: str) -> dict:
        """加载 TVBox 配置"""
        if os.path.exists(url):
            err = self.config.load_from_file(url)
        else:
            err = self.config.load_from_url(url)

        if err:
            return {"ok": False, "error": err}

        self.parse_engine.set_parses(self.config.parses)
        self.spider_mgr.clear_cache()

        config_name = url.split('/')[-1].split('?')[0][:50] if '/' in url else url[:50]
        self.db.add_config(config_name, url)

        return {"ok": True, "summary": self.config.to_summary()}

    def get_sites(self) -> list:
        return [{"key": s.key, "name": s.name, "type": s.type,
                 "searchable": s.searchable, "filterable": s.filterable}
                for s in self.config.sites]

    def get_lives(self) -> list:
        return [{"name": l.name, "type": l.type, "url": l.url, "epg": l.epg}
                for l in self.config.lives]

    def get_parses(self) -> list:
        return self.parse_engine.get_parse_list()

    # ======== 数据库配置管理 ========

    def get_saved_configs(self) -> list:
        return self.db.get_configs()

    def add_config_url(self, name: str, url: str) -> bool:
        return self.db.add_config(name, url)

    def remove_config_url(self, config_id: int):
        self.db.remove_config(config_id)

    def set_active_config_url(self, config_id: int):
        self.db.set_active_config(config_id)

    def get_saved_live_configs(self) -> list:
        return self.db.get_live_configs()

    def add_live_config(self, name: str, url: str, source_type: int = 0) -> bool:
        return self.db.add_live_config(name, url, source_type)

    def remove_live_config(self, config_id: int):
        self.db.remove_live_config(config_id)

    def set_active_live_config(self, config_id: int):
        self.db.set_active_live_config(config_id)

    # ======== 点播功能 ========

    def home_content(self, site_key: str) -> dict:
        site = self.config.get_site(site_key)
        if not site:
            return {"error": "站点不存在"}
        self._current_site_key = site_key
        try:
            data = self.spider_mgr.home_content(site)
            self._current_filters = data.get("filters", {})
            return data
        except Exception as e:
            return {"error": str(e)}

    def get_filters(self, tid: str = "") -> dict:
        if tid and tid in self._current_filters:
            return {"filters": self._current_filters[tid]}
        return {"filters": self._current_filters}

    def category_content(self, site_key: str, tid: str, pg: int = 1, extend: str = "") -> dict:
        site = self.config.get_site(site_key)
        if not site:
            return {"error": "站点不存在"}
        ext = {}
        if extend:
            try:
                ext = json.loads(extend)
            except Exception:
                pass
        try:
            return self.spider_mgr.category_content(site, tid, pg, ext)
        except Exception as e:
            return {"error": str(e)}

    def search_content(self, site_key: str, keyword: str, pg: int = 1) -> dict:
        site = self.config.get_site(site_key)
        if not site:
            return {"error": "站点不存在"}
        try:
            return self.spider_mgr.search_content(site, keyword, pg)
        except Exception as e:
            return {"error": str(e)}

    def search_all(self, keyword: str) -> list:
        """多站点搜索 + 搜索历史记录"""
        self.db.add_search_history(keyword)
        sites = self.config.get_searchable_sites()
        return self.spider_mgr.search_all(sites, keyword)

    def detail_content(self, site_key: str, vod_id: str) -> dict:
        site = self.config.get_site(site_key)
        if not site:
            return {"error": "站点不存在"}
        try:
            return self.spider_mgr.detail_content(site, [vod_id])
        except Exception as e:
            return {"error": str(e)}

    def player_content(self, site_key: str, flag: str, vid: str) -> dict:
        site = self.config.get_site(site_key)
        if not site:
            return {"error": "站点不存在"}
        try:
            result = self.spider_mgr.player_content(site, flag, vid)
            if "header" not in result:
                result["header"] = {}

            parse_flag = result.get("parse", 0)
            if parse_flag == 1:
                play_url = result.get("url", "")
                if play_url:
                    parsed = self.parse_engine.resolve(play_url, flag, parse_flag)
                    if parsed.get("url"):
                        result["url"] = parsed["url"]
                        if parsed.get("header"):
                            result["header"] = parsed["header"]
                        result["parse"] = 0

            return result
        except Exception as e:
            return {"error": str(e)}

    # ======== 搜索历史 ========

    def get_search_history(self, limit: int = 20) -> list:
        return self.db.get_search_history(limit)

    def get_search_suggestions(self, prefix: str, limit: int = 10) -> list:
        return self.db.get_search_suggestions(prefix, limit)

    def clear_search_history(self) -> dict:
        self.db.clear_search_history()
        return {"ok": True}

    def remove_search_history(self, keyword: str) -> dict:
        self.db.remove_search_history(keyword)
        return {"ok": True}

    # ======== 解析器 ========

    def resolve_video(self, url: str, flag: str = "") -> dict:
        return self.parse_engine.resolve(url, flag, 1)

    def sniff_video(self, url: str) -> dict:
        result = self.sniffer.sniff(url)
        if result:
            return {"ok": True, "url": result}
        return {"ok": False, "error": "未找到媒体 URL"}

    # ======== 直播功能 ========

    def parse_live(self, url: str, source_type: int = 0, epg: str = "") -> dict:
        err = self.live_parser.parse(url, source_type, epg)
        if err:
            return {"ok": False, "error": err}
        if epg:
            self.epg.load_async(epg)
        return {"ok": True, "data": self.live_parser.to_dict()}

    def search_live_channels(self, keyword: str) -> list:
        """搜索直播频道"""
        results = []
        for ch in self.live_parser.channels:
            if keyword.lower() in ch.name.lower():
                results.append({
                    "name": ch.name,
                    "url": ch.url,
                    "logo": ch.logo,
                    "group": ch.group,
                })
        return results[:100]

    def get_epg(self, channel_name: str) -> list:
        ch_id = self.epg.match_channel(channel_name)
        if ch_id:
            return self.epg.get_programmes(ch_id)
        return []

    def get_current_epg(self, channel_name: str) -> dict:
        ch_id = self.epg.match_channel(channel_name)
        if ch_id:
            prog = self.epg.get_current_programme(ch_id)
            return prog or {}
        return {}

    def get_epg_info(self) -> dict:
        return self.epg.to_dict()

    # ======== 直播收藏 ========

    def add_live_favorite(self, channel_name: str, channel_url: str,
                          group_name: str = "", logo: str = "") -> dict:
        ok = self.db.add_live_favorite(channel_name, channel_url, group_name, logo)
        return {"ok": ok}

    def remove_live_favorite(self, channel_name: str, channel_url: str) -> dict:
        self.db.remove_live_favorite(channel_name, channel_url)
        return {"ok": True}

    def is_live_favorite(self, channel_name: str, channel_url: str) -> dict:
        return {"is_favorite": self.db.is_live_favorite(channel_name, channel_url)}

    def get_live_favorites(self) -> list:
        return self.db.get_live_favorites()

    # ======== 直播历史 ========

    def add_live_history(self, channel_name: str, channel_url: str, group_name: str = "") -> dict:
        self.db.add_live_history(channel_name, channel_url, group_name)
        return {"ok": True}

    def get_live_history(self, limit: int = 30) -> list:
        return self.db.get_live_history(limit)

    # ======== 播放历史 ========

    def add_history(self, vod_id: str, vod_name: str, vod_pic: str,
                    site_key: str, site_name: str,
                    episode_index: int = 0, episode_name: str = "",
                    play_url: str = "", position: int = 0, duration: int = 0,
                    line_index: int = 0) -> dict:
        self.db.add_history(vod_id, vod_name, vod_pic, site_key, site_name,
                           episode_index, episode_name, play_url, position,
                           duration, line_index)
        return {"ok": True}

    def update_history_position(self, vod_id: str, site_key: str, position: int, duration: int = 0) -> dict:
        self.db.update_history_position(vod_id, site_key, position, duration)
        return {"ok": True}

    def get_history(self, limit: int = 60) -> list:
        return self.db.get_history(limit)

    def get_history_item(self, vod_id: str, site_key: str) -> dict:
        item = self.db.get_history_item(vod_id, site_key)
        return item if item else {}

    def delete_history(self, vod_id: str = "", site_key: str = "", all_records: bool = False) -> dict:
        self.db.delete_history(vod_id if vod_id else None,
                              site_key if site_key else None,
                              all_records)
        return {"ok": True}

    # ======== 收藏 ========

    def add_favorite(self, vod_id: str, vod_name: str, vod_pic: str,
                     site_key: str, site_name: str, vod_remarks: str = "") -> dict:
        ok = self.db.add_favorite(vod_id, vod_name, vod_pic, site_key, site_name, vod_remarks)
        return {"ok": ok}

    def remove_favorite(self, vod_id: str, site_key: str) -> dict:
        self.db.remove_favorite(vod_id, site_key)
        return {"ok": True}

    def is_favorite(self, vod_id: str, site_key: str) -> dict:
        return {"is_favorite": self.db.is_favorite(vod_id, site_key)}

    def get_favorites(self) -> list:
        return self.db.get_favorites()

    # ======== 下载管理 (增强版: 多线程/断点续传/暂停恢复) ========

    def add_download(self, vod_name: str, episode_name: str, url: str,
                     headers: str = "", threads: int = 4) -> dict:
        """添加下载任务 (多线程)"""
        hdrs = {}
        if headers:
            try:
                hdrs = json.loads(headers)
            except Exception:
                pass
        dl_id = self.download_mgr.add_download(vod_name, episode_name, url, hdrs, threads)
        return {"ok": True, "id": dl_id}

    def add_batch_download(self, vod_name: str, episodes_json: str,
                           headers: str = "") -> dict:
        """批量下载
        episodes_json: [{"name":"第1集","url":"http://..."}, ...]
        """
        try:
            episodes = json.loads(episodes_json)
        except Exception:
            return {"ok": False, "error": "episodes JSON 解析失败"}

        hdrs = {}
        if headers:
            try:
                hdrs = json.loads(headers)
            except Exception:
                pass

        task_ids = self.download_mgr.add_batch_download(vod_name, episodes, hdrs)
        return {"ok": True, "ids": task_ids}

    def get_downloads(self) -> list:
        """获取下载列表 (合并数据库和实时状态)"""
        db_downloads = self.db.get_downloads()
        result = []
        for d in db_downloads:
            item = dict(d)
            # 获取实时状态
            live_status = self.download_mgr.get_task_status(d['id'])
            if live_status:
                item['speed'] = live_status.get('speed', 0)
                item['eta'] = live_status.get('eta', 0)
                item['progress'] = live_status.get('progress', 0)
                item['live_status'] = live_status.get('status', d['status'])
            else:
                item['speed'] = 0
                item['eta'] = 0
                total = d.get('file_size', 0)
                item['progress'] = round(d.get('downloaded', 0) / total * 100, 1) if total > 0 else 0
                item['live_status'] = d['status']
            result.append(item)
        return result

    def pause_download(self, download_id: int) -> dict:
        """暂停下载"""
        ok = self.download_mgr.pause_download(download_id)
        return {"ok": ok}

    def resume_download(self, download_id: int) -> dict:
        """恢复下载"""
        ok = self.download_mgr.resume_download(download_id)
        return {"ok": ok}

    def cancel_download(self, download_id: int) -> dict:
        """取消下载"""
        ok = self.download_mgr.cancel_download(download_id)
        return {"ok": ok}

    def retry_download(self, download_id: int) -> dict:
        """重试下载"""
        ok = self.download_mgr.retry_download(download_id)
        return {"ok": ok}

    def remove_download(self, download_id: int) -> dict:
        """移除下载记录"""
        self.download_mgr.cancel_download(download_id)
        self.db.remove_download(download_id)
        return {"ok": True}

    def clear_completed_downloads(self) -> dict:
        """清除已完成的下载"""
        downloads = self.db.get_downloads()
        for d in downloads:
            if d['status'] in ('completed', 'cancelled'):
                self.db.remove_download(d['id'])
        return {"ok": True}

    def set_download_speed_limit(self, limit_kbps: int) -> dict:
        """设置下载速度限制 (KB/s)"""
        self.download_mgr.set_speed_limit(limit_kbps)
        self.db.set_setting('downloadSpeedLimit', str(limit_kbps))
        return {"ok": True}

    def get_download_speed_limit(self) -> dict:
        """获取下载速度限制"""
        limit = int(self.db.get_setting('downloadSpeedLimit', '0'))
        return {"limit_kbps": limit}

    def open_download_folder(self) -> dict:
        """打开下载文件夹"""
        dl_dir = self.download_mgr._get_download_dir()
        try:
            if os.name == 'nt':
                os.startfile(dl_dir)
            else:
                subprocess.Popen(['xdg-open', dl_dir])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ======== 代理服务 ========

    def ensure_proxy(self) -> dict:
        if not self._proxy_started:
            self.proxy.start()
            self._proxy_started = True
        return {"base_url": self.proxy.base_url}

    def build_proxy_url(self, target_url: str, ua: str = "", ref: str = "") -> str:
        self.ensure_proxy()
        return self.proxy.build_proxy_url(target_url, ua, ref)

    # ======== 窗口控制 ========

    def toggle_fullscreen(self) -> dict:
        """切换全屏"""
        if self._window:
            self._window.toggle_fullscreen()
            return {"ok": True}
        return {"ok": False}

    def minimize_to_tray(self) -> dict:
        """最小化到系统托盘"""
        if self._window:
            self._window.hide()
            self._is_minimized_to_tray = True
            if self._tray:
                self._tray.update_tooltip("TVBox Desktop - 后台运行中")
            return {"ok": True}
        return {"ok": False}

    def restore_from_tray(self) -> dict:
        """从托盘恢复窗口"""
        if self._window:
            self._window.show()
            self._is_minimized_to_tray = False
            return {"ok": True}
        return {"ok": False}

    def set_window_always_on_top(self, on_top: bool) -> dict:
        """窗口置顶"""
        if self._window:
            try:
                self._window.on_top = on_top
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return {"ok": False}

    def save_window_state(self, width: int, height: int, x: int, y: int,
                          maximized: bool = False) -> dict:
        """保存窗口状态"""
        self.db.set_setting('windowWidth', str(width))
        self.db.set_setting('windowHeight', str(height))
        self.db.set_setting('windowX', str(x))
        self.db.set_setting('windowY', str(y))
        self.db.set_setting('windowMaximized', '1' if maximized else '0')
        return {"ok": True}

    def get_window_state(self) -> dict:
        """获取窗口状态"""
        return {
            "width": int(self.db.get_setting('windowWidth', '1280')),
            "height": int(self.db.get_setting('windowHeight', '800')),
            "x": int(self.db.get_setting('windowX', '100')),
            "y": int(self.db.get_setting('windowY', '100')),
            "maximized": self.db.get_setting('windowMaximized', '0') == '1',
        }

    def get_clipboard_text(self) -> dict:
        """获取剪贴板文本"""
        try:
            text = clipboard.paste()
            return {"ok": True, "text": text or ""}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_clipboard_text(self, text: str) -> dict:
        """设置剪贴板文本"""
        try:
            clipboard.copy(text)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ======== 开机自启 ========

    def is_autostart_enabled(self) -> dict:
        """检查是否已设置开机自启"""
        return {"enabled": AutoStartManager.is_enabled()}

    def enable_autostart(self) -> dict:
        """启用开机自启"""
        ok = AutoStartManager.enable()
        return {"ok": ok}

    def disable_autostart(self) -> dict:
        """禁用开机自启"""
        ok = AutoStartManager.disable()
        return {"ok": ok}

    # ======== 代理服务 ========

    def get_setting(self, key: str, default: str = "") -> str:
        return self.db.get_setting(key, default)

    def set_setting(self, key: str, value: str) -> dict:
        self.db.set_setting(key, value)
        return {"ok": True}

    def get_all_settings(self) -> dict:
        return self.db.get_all_settings()

    # ======== 截图 ========

    def save_screenshot(self, name: str, timestamp: str, base64_data: str) -> dict:
        """保存截图到本地"""
        import base64 as b64mod
        try:
            # 截图保存目录
            if os.name == 'nt':
                base = os.environ.get('USERPROFILE', os.path.expanduser('~'))
                screenshot_dir = os.path.join(base, 'Pictures', 'TVBoxDesktop')
            else:
                screenshot_dir = os.path.join(os.path.expanduser('~'), 'Pictures', 'TVBoxDesktop')
            os.makedirs(screenshot_dir, exist_ok=True)

            safe_name = "".join(c for c in name if c not in r'\/:*?"<>|')[:50]
            file_path = os.path.join(screenshot_dir, f"{safe_name}_{timestamp}.png")

            with open(file_path, 'wb') as f:
                f.write(b64mod.b64decode(base64_data))

            return {"ok": True, "path": file_path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ======== 弹幕 ========

    def search_danmaku(self, keyword: str) -> list:
        """搜索弹幕 (dandanplay API)"""
        try:
            resp = requests.get(
                "https://api.dandanplay.net/api/v2/search/anime",
                params={"keyword": keyword},
                headers={"User-Agent": "TVBoxDesktop/4.0", "Accept": "application/json"},
                timeout=10
            )
            data = resp.json()
            animes = data.get("Animes", [])
            return [{
                "animeId": a.get("AnimeId"),
                "animeTitle": a.get("AnimeTitle"),
                "type": a.get("Type"),
                "episodes": len(a.get("Episodes", [])),
            } for a in animes[:20]]
        except Exception as e:
            print(f"[Danmaku] 搜索失败: {e}")
            return []

    def load_danmaku(self, anime_id: int, episode: int) -> list:
        """加载弹幕评论"""
        try:
            # 先获取剧集列表
            resp = requests.get(
                f"https://api.dandanplay.net/api/v2/anime/{anime_id}",
                headers={"User-Agent": "TVBoxDesktop/4.0", "Accept": "application/json"},
                timeout=10
            )
            data = resp.json()
            episodes = data.get("Bangumi", {}).get("Episodes", [])
            if not episodes:
                return []

            # 找到对应集数
            ep = None
            for e in episodes:
                if e.get("EpisodeNumber") == episode:
                    ep = e
                    break
            if not ep and episodes:
                ep = episodes[min(episode - 1, len(episodes) - 1)]
            if not ep:
                return []

            episode_id = ep.get("EpisodeId")
            if not episode_id:
                return []

            # 获取弹幕
            resp2 = requests.get(
                f"https://api.dandanplay.net/api/v2/comment/{episode_id}",
                params={"withRelated": "true"},
                headers={"User-Agent": "TVBoxDesktop/4.0", "Accept": "application/json"},
                timeout=15
            )
            data2 = resp2.json()
            comments = data2.get("Comments", [])

            danmaku_list = []
            for c in comments:
                # dandanplay 格式: p = time,mode,color,uid
                p = c.get("p", "")
                parts = p.split(",")
                if len(parts) >= 4:
                    danmaku_list.append({
                        "time": float(parts[0]),
                        "mode": int(parts[1]) if parts[1].isdigit() else 1,
                        "color": parts[2] if not parts[2].startswith("#") else "#ffffff",
                        "text": c.get("text", "") or c.get("m", ""),
                    })
            return danmaku_list
        except Exception as e:
            print(f"[Danmaku] 加载失败: {e}")
            return []

    # ======== 外部播放器 ========

    def open_external_player(self, url: str, flag: str = "") -> dict:
        """用 VLC 或 MPV 打开播放地址"""
        import subprocess
        import shutil

        # 查找可用的播放器
        players = []
        if os.name == 'nt':
            # Windows 常见路径
            vlc_paths = [
                r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            ]
            mpv_paths = [
                r"C:\Program Files\mpv\mpv.exe",
                r"C:\Program Files (x86)\mpv\mpv.exe",
            ]
            for p in vlc_paths:
                if os.path.exists(p):
                    players.append(("vlc", p))
            for p in mpv_paths:
                if os.path.exists(p):
                    players.append(("mpv", p))
            # 也尝试 PATH 中的
            vlc_path = shutil.which("vlc")
            if vlc_path:
                players.append(("vlc", vlc_path))
            mpv_path = shutil.which("mpv")
            if mpv_path:
                players.append(("mpv", mpv_path))
        else:
            vlc_path = shutil.which("vlc")
            if vlc_path:
                players.append(("vlc", vlc_path))
            mpv_path = shutil.which("mpv")
            if mpv_path:
                players.append(("mpv", mpv_path))

        if not players:
            return {"ok": False, "error": "未找到 VLC 或 MPV 播放器"}

        player_type, player_path = players[0]

        try:
            if player_type == "vlc":
                cmd = [player_path, "--no-video-title-show", url]
            else:
                cmd = [player_path, url]

            subprocess.Popen(cmd)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ======== 站点管理 ========

    def toggle_site(self, site_key: str) -> dict:
        """启用/禁用站点"""
        current = self.db.get_setting(f"site_disabled_{site_key}", "0")
        new_val = "0" if current == "1" else "1"
        self.db.set_setting(f"site_disabled_{site_key}", new_val)
        return {"ok": True, "disabled": new_val == "1"}

    def get_site_order(self) -> dict:
        """获取站点排序"""
        order_str = self.db.get_setting("site_order", "")
        if order_str:
            try:
                return {"order": json.loads(order_str)}
            except Exception:
                pass
        return {"order": []}

    def set_site_order(self, order: str) -> dict:
        """保存站点排序"""
        self.db.set_setting("site_order", order)
        return {"ok": True}

    def get_enabled_sites(self) -> list:
        """获取已启用的站点列表"""
        sites = self.get_sites()
        result = []
        for s in sites:
            disabled = self.db.get_setting(f"site_disabled_{s['key']}", "0")
            if disabled != "1":
                result.append(s)
        return result

    # ======== 配置导入导出 ========

    def export_config(self) -> dict:
        """导出所有配置和设置"""
        try:
            export_data = {
                "version": "4.0",
                "configs": self.db.get_configs(),
                "live_configs": self.db.get_live_configs(),
                "settings": self.db.get_all_settings(),
                "favorites": self.db.get_favorites(),
                "live_favorites": self.db.get_live_favorites(),
                "search_history": self.db.get_search_history(100),
            }
            return {"ok": True, "data": json.dumps(export_data, ensure_ascii=False, indent=2)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def import_config(self, json_str: str) -> dict:
        """导入配置"""
        try:
            data = json.loads(json_str)

            # 导入配置地址
            for c in data.get("configs", []):
                self.db.add_config(c.get("name", ""), c.get("url", ""))

            # 导入直播源
            for l in data.get("live_configs", []):
                self.db.add_live_config(l.get("name", ""), l.get("url", ""), l.get("source_type", 0))

            # 导入设置
            for k, v in data.get("settings", {}).items():
                self.db.set_setting(k, v)

            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ======== 工具 ========

    def get_app_info(self) -> dict:
        return {
            "name": "TVBox Desktop",
            "version": "5.0.0",
            "description": "TVBox 兼容的 Windows 桌面播放器",
            "features": [
                "点播浏览", "多站搜索", "搜索历史", "播放历史", "收藏管理",
                "直播电视", "EPG节目单", "直播收藏", "直播历史", "频道搜索",
                "视频解析", "HLS播放", "请求头代理", "多配置管理",
                "全屏播放", "键盘快捷键", "自动连播", "下载管理",
                "主题切换", "画中画", "弹幕支持", "字幕加载",
                "画面比例切换", "视频截图", "外部播放器", "自定义进度条",
                "双击全屏", "滚轮音量", "右键菜单", "配置导入导出",
                "站点管理", "缓冲指示器",
                "系统托盘", "开机自启", "多线程下载", "断点续传",
                "批量下载", "暂停恢复下载", "下载限速", "窗口置顶",
                "剪贴板操作", "窗口状态持久化", "AB回放", "循环播放"
            ],
        }

    def open_external(self, url: str) -> dict:
        """用系统默认浏览器打开 URL"""
        try:
            if os.name == 'nt':
                os.startfile(url)
            else:
                subprocess.Popen(['xdg-open', url])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def shutdown(self):
        """应用关闭清理"""
        self.download_mgr.shutdown()
        if self._tray:
            self._tray.stop()
        if self._proxy_started:
            self.proxy.stop()


def main():
    """启动应用"""
    api = Api()

    # 恢复窗口大小
    win_state = api.get_window_state()

    static_dir = os.path.join(BASE_DIR, "static")
    index_path = os.path.join(static_dir, "index.html")

    window = webview.create_window(
        title="TVBox Desktop v5.0",
        url=index_path,
        js_api=api,
        width=win_state["width"],
        height=win_state["height"],
        x=win_state["x"],
        y=win_state["y"],
        min_size=(1000, 600),
        text_select=True,
    )

    api.set_window(window)

    # 窗口事件处理
    def on_minimizing():
        """最小化时可以选择最小化到托盘"""
        minimize_to_tray = api.db.get_setting('minimizeToTray', '0') == '1'
        if minimize_to_tray:
            api.minimize_to_tray()

    def on_closing():
        """关闭窗口时保存状态"""
        try:
            api.shutdown()
        except Exception:
            pass

    # 绑定窗口事件
    try:
        window.events.minimizing += on_minimizing
        window.events.closing += on_closing
    except Exception:
        pass

    # 启动代理服务器
    api.ensure_proxy()

    # 启动系统托盘 (后台线程)
    def start_tray():
        try:
            tray = SystemTray(
                window=window,
                on_show=lambda: window.show(),
                on_quit=lambda: (api.shutdown(), window.destroy()),
            )
            api._tray = tray
            tray.start()
        except Exception as e:
            print(f"[Tray] 系统托盘启动失败: {e}")

    # 恢复下载速度限制
    speed_limit = api.db.get_setting('downloadSpeedLimit', '0')
    api.download_mgr.set_speed_limit(int(speed_limit))

    # 启动托盘线程
    tray_thread = threading.Thread(target=start_tray, daemon=True)
    tray_thread.start()

    # 启动 webview
    webview.start(debug=False)

    # 应用退出后清理
    try:
        api.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    main()
