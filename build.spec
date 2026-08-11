# -*- mode: python ; coding: utf-8 -*-
"""
TVBox Desktop v5.0 —— PyInstaller 打包配置
生成单文件 EXE, 内嵌 static 资源
包含: pywebview / pystray / requests / quickjs / clipboard
"""

import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('static', 'static'),
    ],
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
        'live',
        'proxy',
        'database',
        'parser',
        'epg',
        'sniffer',
        'downloader',
        'tray',
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
    ],
    hookspath=[],
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
