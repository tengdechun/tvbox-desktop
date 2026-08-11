# -*- mode: python ; coding: utf-8 -*-
"""
TVBox Desktop v5.0 —— PyInstaller 打包配置
生成单文件 EXE, 内嵌 static 资源
包含: pywebview / pystray / requests / quickjs / clipboard / jpype
"""

import os
import sys

block_cipher = None

# ======== 动态检测可选依赖 ========
# JPype1 和 enjarify 是可选的 (JAR 源支持)
# 如果未安装, 不影响 EXE 构建, 只是 JAR 源功能不可用

_optional_hiddenimports = []
_optional_hookspath = []
_optional_binaries = []
_optional_datas = []

try:
    import jpype
    _jpype_dir = os.path.dirname(jpype.__file__)
    # JPype1 自带 PyInstaller hook (hook-jpype.py)
    # 该 hook 负责打包 org.jpype.jar
    _jpype_hook_dir = os.path.join(_jpype_dir, '_pyinstaller')
    if os.path.isdir(_jpype_hook_dir):
        _optional_hookspath.append(_jpype_hook_dir)

    # 列出 jpype 所有子模块
    import pkgutil
    for importer, modname, ispkg in pkgutil.iter_modules(jpype.__path__, 'jpype.'):
        _optional_hiddenimports.append(modname)
    _optional_hiddenimports.append('jpype')

    # 确保 org.jpype.jar 被打包
    _jar_path = os.path.join(os.path.dirname(_jpype_dir), 'org.jpype.jar')
    if os.path.exists(_jar_path):
        _optional_datas.append((_jar_path, '.'))

    print("[build.spec] JPype1 detected, JAR source support enabled")
except ImportError:
    print("[build.spec] JPype1 not installed, JAR source support will be disabled at runtime")

try:
    import enjarify
    _optional_hiddenimports.append('enjarify')
    print("[build.spec] enjarify detected, DEX conversion support enabled")
except ImportError:
    print("[build.spec] enjarify not installed, DEX conversion will use dex2jar only")


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('static', 'static'),
    ] + _optional_datas,
    hiddenimports=[
        # pywebview
        'webview',
        'webview.platforms.edgechromium',
        'webview.platforms.winforms',
        'clr_loader',
        'pythonnet',
        # 爬虫/JS引擎
        'quickjs',
        # 系统托盘
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        # 剪贴板
        'clipboard',
        # 项目模块
        'config',
        'spider',
        'jar_spider',
        'live',
        'proxy',
        'database',
        'parser',
        'epg',
        'sniffer',
        'downloader',
        'tray',
        'network',
        'remote',
        # 第三方库
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        # 标准库
        'gzip',
        'xml.etree.ElementTree',
        'http.server',
        'sqlite3',
        'hashlib',
        'base64',
        'tempfile',
        'subprocess',
        'threading',
        'json',
        'struct',
        'io',
        'zipfile',
        'shutil',
        'winreg',
        'win32com',
        'win32api',
        'win32con',
    ] + _optional_hiddenimports,
    hookspath=[] + _optional_hookspath,
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'unittest',
        'pydoc',
        'doctest',
        'pdb',
        'profile',
        'pstats',
        'matplotlib',
        'numpy',
        'scipy',
        'pandas',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TVBoxDesktop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        'vcruntime140.dll',
        'VCRUNTIME140_1.dll',
        'python3.dll',
        'clr.dll',
        'Python.Runtime.dll',
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='x86_64',
    codesign_identity=None,
    entitlements_file=None,
    icon='static/icon.ico' if os.path.exists('static/icon.ico') else None,
    version='version_info.txt' if os.path.exists('version_info.txt') else None,
)
