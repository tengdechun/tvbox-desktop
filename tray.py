"""
系统托盘支持 —— 最小化到托盘 / 右键菜单 / 开机自启
Windows 原生托盘图标, 支持 pywebview 窗口联动
"""

import os
import sys
import threading
import json
import subprocess
from typing import Callable, Optional

# winreg 只在 Windows 上存在
if os.name == 'nt':
    import winreg
else:
    winreg = None


class SystemTray:
    """Windows 系统托盘"""

    def __init__(self, window=None, on_show=None, on_quit=None,
                 on_play_pause=None, on_next=None, on_prev=None):
        self.window = window
        self.on_show = on_show
        self.on_quit = on_quit
        self.on_play_pause = on_play_pause
        self.on_next = on_next
        self.on_prev = on_prev
        self._icon = None
        self._running = False

    def _create_icon_data(self) -> bytes:
        """创建内嵌的 ICO 图标数据 (32x32 蓝色播放器图标)"""
        import struct
        import io

        # 创建一个简单的 BMP 图标
        width, height = 32, 32
        bpp = 32

        # BGRA 像素数据
        pixels = bytearray()
        for y in range(height - 1, -1, -1):
            for x in range(width):
                # 圆角矩形背景
                cx, cy = x - width // 2, y - height // 2
                dist = (cx * cx + cy * cy) ** 0.5
                if dist < 14:
                    # 蓝色播放按钮
                    if abs(cx) < 5 and cx < 0 and abs(cy) < 7:
                        # 三角形播放图标
                        if cy > cx + 3 and cy < -cx + 10:
                            pixels.extend([255, 255, 255, 255])  # 白色三角
                        else:
                            pixels.extend([24, 144, 255, 255])   # 蓝色背景
                    elif cx >= 0 and abs(cy) < 7 and cx < 8:
                        if cy > -cx + 1 and cy < cx + 7:
                            pixels.extend([255, 255, 255, 255])
                        else:
                            pixels.extend([24, 144, 255, 255])
                    else:
                        pixels.extend([24, 144, 255, 255])
                else:
                    pixels.extend([0, 0, 0, 0])  # 透明

        # BMP 信息头
        bmp_header = struct.pack('<IIIHHIIIIII',
            40,           # biSize
            width,        # biWidth
            height * 2,   # biHeight (icon = 2x)
            1,            # biPlanes
            bpp,          # biBitCount
            0, 0, 0, 0, 0, 0)

        # DIB 数据
        dib_data = bmp_header + bytes(pixels)

        # AND mask (全0 = 不透明)
        and_mask = b'\x00' * ((width * height) // 8)

        # ICONDIR
        icon_dir = struct.pack('<HHH', 0, 1, 1)
        # ICONDIRENTRY
        icon_entry = struct.pack('<BBBBHHII',
            width if width < 256 else 0,
            height if height < 256 else 0,
            0, 0,
            1,
            bpp,
            len(dib_data) + len(and_mask),
            6 + 16)  # offset

        return icon_dir + icon_entry + dib_data + and_mask

    def _get_icon_path(self) -> str:
        """获取图标路径, 优先使用外部文件, 否则创建临时文件"""
        # 尝试使用内置图标
        if getattr(sys, 'frozen', False):
            base = sys._MEIPASS
        else:
            base = os.path.dirname(os.path.abspath(__file__))

        icon_path = os.path.join(base, 'static', 'icon.ico')
        if os.path.exists(icon_path):
            return icon_path

        # 创建临时图标文件
        icon_data = self._create_icon_data()
        temp_path = os.path.join(os.environ.get('TEMP', '/tmp'), 'tvbox_tray.ico')
        with open(temp_path, 'wb') as f:
            f.write(icon_data)
        return temp_path

    def start(self):
        """启动系统托盘"""
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            print("[Tray] pystray/Pillow 未安装, 系统托盘不可用")
            return False

        icon_path = self._get_icon_path()

        try:
            image = Image.open(icon_path)
        except Exception:
            # 创建备用图标
            image = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle([4, 4, 28, 28], radius=8, fill=(24, 144, 255, 255))
            draw.polygon([(12, 10), (12, 22), (23, 16)], fill=(255, 255, 255, 255))

        # 创建菜单
        menu_items = [
            pystray.MenuItem('显示主窗口', self._on_show, default=True),
            pystray.MenuItem('播放/暂停', self._on_play_pause) if self.on_play_pause else None,
            pystray.MenuItem('上一集', self._on_prev) if self.on_prev else None,
            pystray.MenuItem('下一集', self._on_next) if self.on_next else None,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('开机自启动', self._on_toggle_autostart, checked=self._is_autostart_enabled),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('退出', self._on_quit),
        ]
        menu = pystray.Menu(*[m for m in menu_items if m is not None])

        self._icon = pystray.Icon(
            "TVBoxDesktop",
            image,
            "TVBox Desktop",
            menu
        )

        self._running = True
        self._icon.run()
        return True

    def stop(self):
        """停止托盘"""
        self._running = False
        if self._icon:
            self._icon.stop()

    def update_tooltip(self, text: str):
        """更新托盘提示文字"""
        if self._icon:
            self._icon.title = text

    def _on_show(self, icon=None, item=None):
        """显示主窗口"""
        if self.window:
            try:
                self.window.show()
                if hasattr(self.window, 'restore'):
                    self.window.restore()
            except Exception:
                pass
        if self.on_show:
            self.on_show()

    def _on_quit(self, icon=None, item=None):
        """退出应用"""
        if self.on_quit:
            self.on_quit()
        self.stop()

    def _on_play_pause(self, icon=None, item=None):
        if self.on_play_pause:
            self.on_play_pause()

    def _on_next(self, icon=None, item=None):
        if self.on_next:
            self.on_next()

    def _on_prev(self, icon=None, item=None):
        if self.on_prev:
            self.on_prev()

    def _is_autostart_enabled(self, item=None) -> bool:
        """检查是否已设置开机自启"""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_READ
            )
            val, _ = winreg.QueryValueEx(key, "TVBoxDesktop")
            winreg.CloseKey(key)
            return bool(val)
        except FileNotFoundError:
            return False
        except Exception:
            return False

    def _on_toggle_autostart(self, icon=None, item=None):
        """切换开机自启"""
        if self._is_autostart_enabled():
            self._disable_autostart()
        else:
            self._enable_autostart()

    def _enable_autostart(self):
        """启用开机自启"""
        try:
            if getattr(sys, 'frozen', False):
                exe_path = sys.executable
            else:
                exe_path = os.path.abspath(sys.argv[0])

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE
            )
            winreg.SetValueEx(key, "TVBoxDesktop", 0, winreg.REG_SZ, f'"{exe_path}"')
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[Tray] 设置开机自启失败: {e}")

    def _disable_autostart(self):
        """禁用开机自启"""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE
            )
            winreg.DeleteValue(key, "TVBoxDesktop")
            winreg.CloseKey(key)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[Tray] 取消开机自启失败: {e}")


class AutoStartManager:
    """开机自启管理 (跨平台)"""

    @staticmethod
    def is_enabled() -> bool:
        if os.name == 'nt':
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0, winreg.KEY_READ
                )
                val, _ = winreg.QueryValueEx(key, "TVBoxDesktop")
                winreg.CloseKey(key)
                return bool(val)
            except Exception:
                return False
        elif sys.platform == 'darwin':
            plist = os.path.expanduser('~/Library/LaunchAgents/com.tvbox.desktop.plist')
            return os.path.exists(plist)
        else:
            desktop = os.path.expanduser('~/.config/autostart/tvbox-desktop.desktop')
            return os.path.exists(desktop)

    @staticmethod
    def enable() -> bool:
        if os.name == 'nt':
            try:
                exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0])
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0, winreg.KEY_SET_VALUE
                )
                winreg.SetValueEx(key, "TVBoxDesktop", 0, winreg.REG_SZ, f'"{exe_path}"')
                winreg.CloseKey(key)
                return True
            except Exception:
                return False
        elif sys.platform == 'darwin':
            try:
                plist_dir = os.path.expanduser('~/Library/LaunchAgents')
                os.makedirs(plist_dir, exist_ok=True)
                plist_path = os.path.join(plist_dir, 'com.tvbox.desktop.plist')
                exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0])
                plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.tvbox.desktop</string>
    <key>ProgramArguments</key><array><string>{exe_path}</string></array>
    <key>RunAtLoad</key><true/>
</dict>
</plist>"""
                with open(plist_path, 'w') as f:
                    f.write(plist_content)
                return True
            except Exception:
                return False
        else:
            try:
                autostart_dir = os.path.expanduser('~/.config/autostart')
                os.makedirs(autostart_dir, exist_ok=True)
                desktop_path = os.path.join(autostart_dir, 'tvbox-desktop.desktop')
                exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0])
                desktop_content = f"""[Desktop Entry]
Type=Application
Name=TVBox Desktop
Exec={exe_path}
Terminal=false
X-GNOME-Autostart-enabled=true"""
                with open(desktop_path, 'w') as f:
                    f.write(desktop_content)
                return True
            except Exception:
                return False

    @staticmethod
    def disable() -> bool:
        if os.name == 'nt':
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0, winreg.KEY_SET_VALUE
                )
                winreg.DeleteValue(key, "TVBoxDesktop")
                winreg.CloseKey(key)
                return True
            except FileNotFoundError:
                return True
            except Exception:
                return False
        elif sys.platform == 'darwin':
            plist = os.path.expanduser('~/Library/LaunchAgents/com.tvbox.desktop.plist')
            try:
                os.remove(plist)
                return True
            except Exception:
                return True
        else:
            desktop = os.path.expanduser('~/.config/autostart/tvbox-desktop.desktop')
            try:
                os.remove(desktop)
                return True
            except Exception:
                return True
