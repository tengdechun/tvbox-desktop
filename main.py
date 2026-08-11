"""
TVBox Desktop v5.0 —— 主入口
pywebview 窗口 + Python API 桥接 + 系统托盘 + 多线程下载
兼容 PyInstaller 打包
"""

import os
import sys
import json
import time
import socket
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
from network import HttpClient, SimpleConverter, AdBlocker, SniffRuleManager, DohResolver
from remote import RemoteServer


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

        # ======== 网络层组件 ========
        self.ad_blocker = AdBlocker()         # 广告拦截器 (与 HttpClient 共享)
        self.http_client = HttpClient(        # 统一 HTTP 客户端
            doh_resolver=DohResolver(),
            ad_blocker=self.ad_blocker,
        )
        self.converter = SimpleConverter()    # 繁简转换
        self.sniff_rules = SniffRuleManager() # 嗅探规则管理

        # ======== 远程控制 ========
        self.remote_server = RemoteServer(api=self)  # 本地 HTTP API

        self._proxy_started = False
        self._current_site_key = None
        self._current_filters = {}
        self._window = None
        self._tray = None
        self._is_minimized_to_tray = False

        # ======== 播放状态 / 无痕模式 ========
        self._media_status = {}      # 当前播放状态 (前端上报)
        self._incognito = False      # 无痕模式

        # 注册下载进度回调 -> 通知前端
        self.download_mgr.add_progress_callback(self._on_download_progress)

        # 恢复无痕模式设置
        try:
            self._incognito = self.db.get_setting("incognito", "0") == "1"
        except Exception:
            self._incognito = False

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

        # 根据配置重新配置网络组件 (DoH / 代理 / 广告拦截 / Hosts / 嗅探规则)
        self._reconfigure_network()

        # 应用数据库中保存的站点覆盖 (隐藏等)
        self._apply_site_overrides()

        return {"ok": True, "summary": self.config.to_summary()}

    def _reconfigure_network(self):
        """根据当前 config 重新配置网络组件 (DoH / 代理 / 广告拦截 / Hosts / 嗅探规则)"""
        try:
            # DoH
            doh_urls = [d.url for d in self.config.doh if d.url]
            if doh_urls and self.http_client.doh:
                self.http_client.doh.doh_urls = doh_urls

            # 代理规则
            proxy_rules = []
            for p in self.config.proxies:
                if p.host and p.port:
                    proxy_rules.append({
                        "host": p.rule or ".*",
                        "proxy": p.to_url(),
                    })
            if self.config.proxy:
                self.http_client.proxy.default_proxy = self.config.proxy
            if proxy_rules:
                self.http_client.proxy.set_rules(proxy_rules)

            # 广告拦截 —— 合并默认黑名单与配置黑名单
            if self.config.ads:
                merged = list(self.ad_blocker.DEFAULT_BLOCK_DOMAINS) + list(self.config.ads)
                self.ad_blocker.set_block_domains(merged)

            # Hosts 解析覆盖
            if self.config.hosts:
                hosts_entries = []
                for h in self.config.hosts:
                    if isinstance(h, dict):
                        ip = h.get("ip", "")
                        host = h.get("host", "")
                        if ip and host:
                            hosts_entries.append(ip + " " + host)
                    elif isinstance(h, str) and h:
                        hosts_entries.append(h)
                if hosts_entries:
                    self.http_client.hosts.load_entries(hosts_entries)

            # 嗅探规则 —— 从配置的 rules 加载
            if self.config.rules:
                self.sniff_rules.clear()
                for r in self.config.rules:
                    self.sniff_rules.add_rule(r.to_dict())
        except Exception as e:
            print(f"[Network] 重新配置网络组件失败: {e}")

    def _apply_site_overrides(self):
        """应用数据库中保存的站点覆盖 (隐藏 / 禁用)"""
        try:
            for s in self.config.sites:
                hidden = self.db.get_setting("site_hidden_" + s.key, "")
                if hidden:
                    s.hide = int(hidden)
        except Exception:
            pass

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

    # ======== 网络配置查询 ========

    def get_doh_list(self) -> list:
        """返回 DoH 配置"""
        try:
            return [d.to_dict() for d in self.config.doh]
        except Exception:
            return []

    def get_proxy_list(self) -> list:
        """返回代理配置"""
        try:
            result = [p.to_dict() for p in self.config.proxies]
            if not result and self.config.proxy:
                result = [{"name": "default", "url": self.config.proxy}]
            return result
        except Exception:
            return []

    def get_rules(self) -> list:
        """返回网络拦截规则"""
        try:
            return [r.to_dict() for r in self.config.rules]
        except Exception:
            return []

    def get_ads(self) -> list:
        """返回广告域名黑名单"""
        try:
            return list(self.config.ads)
        except Exception:
            return []

    def get_hosts(self) -> dict:
        """返回 hosts 解析覆盖"""
        try:
            return {"hosts": self.config.hosts}
        except Exception:
            return {"hosts": []}

    def get_headers_config(self) -> list:
        """返回 CORS 注入配置"""
        try:
            return [h.to_dict() for h in self.config.headers]
        except Exception:
            return []

    def is_ad_url(self, url: str) -> dict:
        """检查 URL 是否是广告"""
        try:
            return {"is_ad": self.ad_blocker.is_ad(url), "url": url}
        except Exception as e:
            return {"is_ad": False, "url": url, "error": str(e)}

    def resolve_host(self, host: str) -> dict:
        """DoH 解析域名"""
        try:
            # 优先检查 hosts 覆盖
            hosts_ip = self.config.resolve_host(host)
            if hosts_ip:
                return {"host": host, "ip": hosts_ip, "source": "hosts"}
            # 使用 DoH 解析
            if self.http_client.doh:
                ip = self.http_client.doh.resolve(host)
                if ip:
                    return {"host": host, "ip": ip, "source": "doh"}
            # 回退到系统 DNS
            try:
                ip = socket.gethostbyname(host)
                if ip:
                    return {"host": host, "ip": ip, "source": "system"}
            except Exception:
                pass
            return {"host": host, "ip": "", "source": "none", "error": "无法解析"}
        except Exception as e:
            return {"host": host, "ip": "", "error": str(e)}

    def t2s(self, text: str) -> dict:
        """繁体转简体"""
        try:
            return {"ok": True, "text": self.converter.to_simple(text)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def s2t(self, text: str) -> dict:
        """简体转繁体"""
        try:
            return {"ok": True, "text": self.converter.to_traditional(text)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ======== 远程控制 ========

    def get_remote_url(self) -> dict:
        """返回本地 HTTP API 地址"""
        try:
            return {"ok": True, "url": self.remote_server.get_url()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_remote_port(self) -> dict:
        """返回本地 HTTP API 端口"""
        try:
            return {"ok": True, "port": self.remote_server.get_port()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_device_info(self) -> dict:
        """返回设备信息"""
        try:
            import platform
            return {
                "ok": True,
                "os": platform.system(),
                "os_version": platform.version(),
                "machine": platform.machine(),
                "hostname": socket.gethostname(),
                "python": platform.python_version(),
                "app_name": "TVBox Desktop",
                "app_version": "5.0.0",
                "remote_url": self.remote_server.get_url(),
                "remote_port": self.remote_server.get_port(),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_media_status(self) -> dict:
        """返回当前播放状态"""
        try:
            return {"ok": True, "status": self._media_status}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_media_status(self, status_json: str) -> dict:
        """更新播放状态 (前端调用上报)"""
        try:
            status = json.loads(status_json) if status_json else {}
            self._media_status = status
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ======== 壁纸 / 公告 / Logo ========

    def get_wallpaper(self) -> dict:
        """返回壁纸 URL"""
        try:
            url = self.config.wallpaper or self.db.get_setting("wallpaper", "")
            return {"ok": True, "url": url}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_notice(self) -> dict:
        """返回启动公告"""
        try:
            notice = self.config.notice or self.db.get_setting("notice", "")
            return {"ok": True, "notice": notice}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_logo(self) -> dict:
        """返回自定义 Logo"""
        try:
            logo = self.config.logo or self.db.get_setting("logo", "")
            return {"ok": True, "logo": logo}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ======== 无痕模式 ========

    def is_incognito(self) -> dict:
        """返回无痕模式状态"""
        try:
            return {"ok": True, "enabled": self._incognito}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_incognito(self, enabled: bool) -> dict:
        """设置无痕模式"""
        try:
            self._incognito = bool(enabled)
            self.db.set_setting("incognito", "1" if self._incognito else "0")
            return {"ok": True, "enabled": self._incognito}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ======== 集数解析 ========

    def parse_episodes(self, vod_play_from: str, vod_play_url: str) -> dict:
        """解析集数
        返回: {lines: [{name, episodes: [{name, url}]}], line_count, episode_count}
        支持 $$$ 分隔线路, # 分隔集数, $ 分隔名称 URL
        <300 集自动倒序
        """
        try:
            lines = []
            from_list = vod_play_from.split("$$$") if vod_play_from else []
            url_list = vod_play_url.split("$$$") if vod_play_url else []

            total_episodes = 0
            for i, url_str in enumerate(url_list):
                line_name = from_list[i] if i < len(from_list) else ("线路" + str(i + 1))
                episodes = []
                for ep in url_str.split("#"):
                    if not ep:
                        continue
                    parts = ep.split("$", 1)
                    if len(parts) == 2:
                        ep_name, ep_url = parts[0], parts[1]
                    else:
                        ep_name = "第" + str(len(episodes) + 1) + "集"
                        ep_url = parts[0]
                    if ep_url:
                        episodes.append({"name": ep_name, "url": ep_url})
                # <300 集自动倒序
                if 0 < len(episodes) < 300:
                    episodes.reverse()
                total_episodes += len(episodes)
                lines.append({"name": line_name, "episodes": episodes})

            return {
                "ok": True,
                "lines": lines,
                "line_count": len(lines),
                "episode_count": total_episodes,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ======== 播放失败换源 ========

    def auto_switch_source(self, site_key: str, vod_id: str,
                           flag: str, vid: str) -> dict:
        """播放失败换源
        逻辑: 尝试其他线路 -> 尝试其他站点搜索同名 -> 返回可用的播放 URL
        """
        try:
            site = self.config.get_site(site_key)
            if not site:
                return {"ok": False, "error": "站点不存在"}

            vod_name = ""

            # 1. 尝试其他线路
            try:
                detail = self.spider_mgr.detail_content(site, [vod_id])
                vod_list = detail.get("list", []) if detail else []
                if vod_list:
                    vod = vod_list[0]
                    vod_name = vod.get("vod_name", "")
                    play_from = vod.get("vod_play_from", "").split("$$$")
                    play_url_raw = vod.get("vod_play_url", "").split("$$$")

                    current_idx = -1
                    for i, pf in enumerate(play_from):
                        if pf == flag:
                            current_idx = i
                            break

                    for i, pf in enumerate(play_from):
                        if i == current_idx or i >= len(play_url_raw):
                            continue
                        for ep in play_url_raw[i].split("#"):
                            parts = ep.split("$", 1)
                            ep_url = parts[1] if len(parts) == 2 else parts[0]
                            if not ep_url:
                                continue
                            try:
                                pr = self.spider_mgr.player_content(site, pf, ep_url)
                                if pr and pr.get("url"):
                                    return {
                                        "ok": True,
                                        "site_key": site_key,
                                        "flag": pf,
                                        "vid": ep_url,
                                        "url": pr["url"],
                                        "header": pr.get("header", {}),
                                        "source": "other_line",
                                    }
                            except Exception:
                                continue
            except Exception:
                pass

            # 2. 尝试其他站点搜索同名
            if vod_name:
                for other_site in self.config.get_searchable_sites():
                    if other_site.key == site_key:
                        continue
                    try:
                        sr = self.spider_mgr.search_content(other_site, vod_name, 1)
                        if not sr:
                            continue
                        for item in sr.get("list", [])[:3]:
                            if vod_name not in (item.get("vod_name") or ""):
                                continue
                            other_vod_id = item.get("vod_id", "")
                            if not other_vod_id:
                                continue
                            od = self.spider_mgr.detail_content(other_site, [other_vod_id])
                            other_vods = od.get("list", []) if od else []
                            if not other_vods:
                                continue
                            opf = other_vods[0].get("vod_play_from", "").split("$$$")
                            opu = other_vods[0].get("vod_play_url", "").split("$$$")
                            for i, pf in enumerate(opf):
                                if i >= len(opu):
                                    continue
                                for ep in opu[i].split("#"):
                                    parts = ep.split("$", 1)
                                    ep_url = parts[1] if len(parts) == 2 else parts[0]
                                    if not ep_url:
                                        continue
                                    try:
                                        pr = self.spider_mgr.player_content(other_site, pf, ep_url)
                                        if pr and pr.get("url"):
                                            return {
                                                "ok": True,
                                                "site_key": other_site.key,
                                                "site_name": other_site.name,
                                                "flag": pf,
                                                "vid": ep_url,
                                                "url": pr["url"],
                                                "header": pr.get("header", {}),
                                                "source": "other_site",
                                            }
                                    except Exception:
                                        continue
                    except Exception:
                        continue

            return {"ok": False, "error": "无可用换源"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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

    def get_danmaku_api(self) -> dict:
        """返回配置的弹幕 API URL"""
        try:
            url = self.config.danmaku or self.db.get_setting("danmakuApi", "")
            return {"ok": True, "url": url}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_danmaku_api(self, url: str) -> dict:
        """设置弹幕 API"""
        try:
            self.db.set_setting("danmakuApi", url)
            self.config.danmaku = url
            return {"ok": True, "url": url}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def search_danmaku_multi(self, keyword: str, episode: int = 1) -> list:
        """多来源弹幕搜索
        来源: dandanplay + 自定义弹幕 API
        """
        results = []
        # 1. dandanplay
        try:
            dandan = self.search_danmaku(keyword)
            for item in dandan:
                item["source"] = "dandanplay"
                results.append(item)
        except Exception:
            pass

        # 2. 自定义弹幕 API
        try:
            api_url = self.config.danmaku or self.db.get_setting("danmakuApi", "")
            if api_url:
                data = self.http_client.get_json(
                    api_url,
                    params={"keyword": keyword, "episode": episode},
                )
                if data:
                    if isinstance(data, list):
                        items = data
                    elif isinstance(data, dict):
                        items = data.get("list") or data.get("animes") or data.get("data") or []
                    else:
                        items = []
                    for item in items:
                        if isinstance(item, dict):
                            item["source"] = "custom"
                            results.append(item)
        except Exception:
            pass

        return results

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

    def get_visible_sites(self) -> list:
        """返回未隐藏的站点 (同时过滤已禁用的)"""
        try:
            result = []
            for s in self.config.sites:
                # 检查数据库中的隐藏覆盖
                hidden = self.db.get_setting("site_hidden_" + s.key, str(s.hide))
                if hidden == "1":
                    continue
                disabled = self.db.get_setting("site_disabled_" + s.key, "0")
                if disabled == "1":
                    continue
                result.append({
                    "key": s.key, "name": s.name, "type": s.type,
                    "searchable": s.searchable, "filterable": s.filterable,
                    "hide": s.hide,
                })
            return result
        except Exception:
            return []

    def toggle_site_hide(self, site_key: str) -> dict:
        """切换站点隐藏状态"""
        try:
            site = self.config.get_site(site_key)
            if not site:
                return {"ok": False, "error": "站点不存在"}
            current = self.db.get_setting("site_hidden_" + site_key, str(site.hide))
            new_hidden = "0" if current == "1" else "1"
            self.db.set_setting("site_hidden_" + site_key, new_hidden)
            site.hide = int(new_hidden)
            return {"ok": True, "hidden": site.hide == 1, "site_key": site_key}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def reorder_sites(self, order_json: str) -> dict:
        """重新排序站点
        order_json: ["site_key1", "site_key2", ...]
        """
        try:
            order = json.loads(order_json)
            if not isinstance(order, list):
                return {"ok": False, "error": "order 必须是列表"}
            self.db.set_setting("site_order", json.dumps(order, ensure_ascii=False))
            # 重新排序内存中的站点列表
            order_map = {key: i for i, key in enumerate(order)}
            self.config.sites.sort(
                key=lambda s: order_map.get(s.key, len(order))
            )
            # 重建 site_map
            self.config._site_map = {s.key: s for s in self.config.sites}
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ======== Catchup 时移 ========

    def build_catchup_url(self, channel_name: str, channel_url: str,
                          start_time: str, end_time: str) -> dict:
        """根据频道的 catchup 配置构建时移 URL"""
        try:
            import re as _re
            from datetime import datetime

            # 查找频道
            channel = None
            for ch in self.live_parser.channels:
                if ch.name == channel_name:
                    channel = ch
                    break
                all_urls = ch.all_urls() if hasattr(ch, "all_urls") else [ch.url]
                if channel_url in all_urls:
                    channel = ch
                    break
            if not channel:
                return {"ok": False, "error": "频道不存在"}

            # 获取 catchup 配置 (优先频道级, 回退全局)
            catchup_source = channel.catchup_source or self.live_parser.global_catchup_source
            catchup_type = channel.catchup_type or self.live_parser.global_catchup
            catchup_replace = channel.catchup_replace or self.live_parser.global_catchup_replace

            if not catchup_source:
                return {"ok": False, "error": "频道未配置 catchup"}

            # 解析时间 (支持 ISO 格式或 Unix 时间戳)
            start_dt = None
            end_dt = None
            for parser_fn in (self._parse_dt_iso, self._parse_dt_ts):
                start_dt = parser_fn(start_time)
                end_dt = parser_fn(end_time)
                if start_dt and end_dt:
                    break
            if not start_dt or not end_dt:
                return {"ok": False, "error": "时间格式无效"}

            start_utc = int(start_dt.timestamp())
            end_utc = int(end_dt.timestamp())

            # 应用 catchup-replace (从原 URL 提取变量替换到 source 模板)
            result = catchup_source
            if catchup_replace and channel.catchup_regex:
                pairs = catchup_replace.split(",")
                try:
                    m = _re.search(channel.catchup_regex, channel_url)
                    if m:
                        groups = m.groups()
                        for i in range(0, min(len(pairs) - 1, len(groups)), 2):
                            val_key = pairs[i]
                            val_idx = int(pairs[i + 1]) if pairs[i + 1].isdigit() else 0
                            if val_idx < len(groups) and groups[val_idx]:
                                result = result.replace(val_key, groups[val_idx])
                except Exception:
                    pass

            # 替换时间变量 {(b)fmt} -> 开始时间格式化
            result = _re.sub(
                r"\{\(b\)([^}]+)\}",
                lambda m: start_dt.strftime(m.group(1)),
                result,
            )
            # {(e)fmt} -> 结束时间格式化
            result = _re.sub(
                r"\{\(e\)([^}]+)\}",
                lambda m: end_dt.strftime(m.group(1)),
                result,
            )
            # {utc:offset} -> 开始 Unix 秒 + 偏移
            result = _re.sub(
                r"\{utc:(-?\d+)\}",
                lambda m: str(start_utc + int(m.group(1))),
                result,
            )
            # {utcend:offset} -> 结束 Unix 秒 + 偏移
            result = _re.sub(
                r"\{utcend:(-?\d+)\}",
                lambda m: str(end_utc + int(m.group(1))),
                result,
            )
            # 无偏移简写
            result = result.replace("{utc}", str(start_utc))
            result = result.replace("{utcend}", str(end_utc))

            return {
                "ok": True,
                "url": result,
                "start_utc": start_utc,
                "end_utc": end_utc,
                "catchup_type": catchup_type,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _parse_dt_iso(time_str: str):
        """尝试用 ISO 格式解析时间"""
        from datetime import datetime
        try:
            return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        except Exception:
            return None

    @staticmethod
    def _parse_dt_ts(time_str: str):
        """尝试用 Unix 时间戳解析时间"""
        from datetime import datetime
        try:
            return datetime.fromtimestamp(float(time_str))
        except Exception:
            return None

    # ======== 嗅探规则 ========

    def get_sniff_rules(self) -> list:
        """返回嗅探规则"""
        try:
            # 若管理器为空且有配置规则, 则从配置加载
            if not self.sniff_rules.rules and self.config.rules:
                for r in self.config.rules:
                    self.sniff_rules.add_rule(r.to_dict())
            return [r.to_dict() for r in self.sniff_rules.rules]
        except Exception:
            return []

    def add_sniff_rule(self, rule_json: str) -> dict:
        """添加嗅探规则
        rule_json: {"host": "...", "regex": [...], "script": "...", "exclude": [...]}
        """
        try:
            rule = json.loads(rule_json) if isinstance(rule_json, str) else rule_json
            self.sniff_rules.add_rule(rule)
            return {"ok": True, "count": len(self.sniff_rules.rules)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def sniff_with_rules(self, url: str) -> dict:
        """使用规则嗅探媒体 URL"""
        try:
            # 获取页面内容
            text = self.http_client.get_text(url)
            if not text:
                return {"ok": False, "error": "获取页面内容失败"}
            # 使用规则提取 URL
            media_urls = self.sniff_rules.extract_urls(text, url)
            if not media_urls:
                # 回退到默认嗅探器
                sniffed = self.sniffer.sniff(url)
                if sniffed:
                    media_urls = [sniffed]
            if media_urls:
                return {"ok": True, "urls": media_urls, "url": media_urls[0]}
            return {"ok": False, "error": "未找到媒体 URL"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

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
        # 停止下载管理器
        try:
            self.download_mgr.shutdown()
        except Exception:
            pass

        # 停止远程控制服务器
        try:
            if self.remote_server:
                self.remote_server.stop()
        except Exception:
            pass

        # 关闭 HTTP 客户端 session (恢复 DNS)
        try:
            if self.http_client:
                self.http_client.close()
        except Exception:
            pass

        # 停止 EPG 定时刷新
        try:
            self.epg.cancel_refresh()
        except Exception:
            pass

        # 停止系统托盘
        try:
            if self._tray:
                self._tray.stop()
        except Exception:
            pass

        # 停止代理服务器
        try:
            if self._proxy_started:
                self.proxy.stop()
        except Exception:
            pass


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

    # 启动 RemoteServer (独立线程, 提供本地 HTTP API 供远程控制)
    def start_remote():
        try:
            api.remote_server.start()
            url = api.remote_server.get_url()
            if url:
                print("[RemoteServer] 已启动: " + url)
            else:
                print("[RemoteServer] 启动失败: 未获取到有效地址")
        except Exception as e:
            print("[RemoteServer] 启动失败: " + str(e))

    remote_thread = threading.Thread(target=start_remote, daemon=True, name="RemoteServer")
    remote_thread.start()

    # 注入初始配置 (壁纸 / 公告 / Logo / 弹幕) 到前端
    def inject_initial_config():
        try:
            time.sleep(1.5)  # 等待前端 JS 就绪
            if not window:
                return
            init_data = {
                "wallpaper": api.config.wallpaper or api.db.get_setting("wallpaper", ""),
                "notice": api.config.notice or api.db.get_setting("notice", ""),
                "logo": api.config.logo or api.db.get_setting("logo", ""),
                "danmaku": api.config.danmaku or api.db.get_setting("danmakuApi", ""),
                "incognito": api._incognito,
            }
            data_str = json.dumps(init_data, ensure_ascii=False)
            js_code = (
                'if (window.App && App.setInitialConfig) {'
                'App.setInitialConfig(' + data_str + ');'
                '}'
            )
            window.evaluate_js(js_code)
        except Exception:
            pass

    inject_thread = threading.Thread(target=inject_initial_config, daemon=True,
                                     name="InitConfig")
    inject_thread.start()

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
