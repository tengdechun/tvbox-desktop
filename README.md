# TVBox Desktop v5.0

基于 TVBox / CatVod 配置规范的 Windows 桌面播放器，可打包为独立 EXE 单文件。
完整复刻 TVBox 核心功能，支持点播、直播、收藏、历史、下载、弹幕等全部特性。

## 功能特性

### 点播浏览
- **配置兼容**: TVBox / FongMi 格式 JSON 配置，支持多配置管理与切换
- **站点浏览**: 分类浏览、筛选器、分页、网格密度调节 (标准/紧凑/大图)
- **多站搜索**: 并行搜索所有站点，支持搜索历史和智能前缀建议
- **视频详情**: 海报、简介、演职员、多线路选集
- **播放历史**: 自动记录播放进度，支持断点续播
- **收藏管理**: 影视收藏 + 直播收藏双模式
- **站点管理**: 启用/禁用单个站点，自定义站点排序

### 播放器 (专业级控制)
- **HLS 播放**: hls.js 支持 m3u8 流，自动错误恢复
- **直链播放**: mp4/flv/ts/mkv 等直链格式
- **请求头代理**: 内置代理解决 User-Agent/Referer 限制
- **倍速控制**: 0.5x ~ 3.0x 变速播放
- **进度跳转**: 快进/快退 10 秒，自定义进度条拖拽
- **音量控制**: 滑块调节 + 静音切换，记忆音量设置
- **全屏播放**: Fullscreen API 支持，双击全屏
- **画中画**: Picture-in-Picture 模式
- **自动连播**: 播放完当前集自动播放下一集
- **进度记忆**: 恢复上次播放位置
- **AB 回放**: 设置 A/B 点循环播放片段
- **循环播放**: 单集循环 / 列表循环 / 不循环
- **画面比例**: 默认/16:9/4:3/拉伸/原始，右键菜单快速切换
- **视频截图**: 一键截图保存到本地
- **字幕加载**: 支持 SRT/ASS/VTT 字幕文件
- **弹幕支持**: 集成 dandanplay API，弹幕开关/透明度/速度/字号设置
- **缓冲指示**: 加载中显示 spinner 动画
- **视频信息**: 操作时显示 OSD 信息覆盖层
- **右键菜单**: 截图/字幕/弹幕/画面比例/播放速度/AB回放/循环/外部播放器
- **外部播放器**: 支持 VLC / MPV 打开
- **视频解析**: VIP 视频解析器，嗅探模式，多种 JSON 返回格式
- **键盘快捷键**: 空格/方向键/F/N/P/M/D/R/S/Esc 全部支持

### 直播电视
- **多格式**: M3U / TXT(#genre#) / JSON
- **EPG 节目单**: XMLTV 格式，支持 .gz 压缩，异步加载
- **频道分组**: 按分组浏览频道
- **频道搜索**: 实时搜索频道名称
- **直播收藏**: 收藏喜欢的频道
- **直播历史**: 记录最近观看的频道
- **频道切换**: 上一台/下一台快捷切换

### 下载管理 (增强版)
- **多线程下载**: 支持 1/2/4/8 线程并行下载
- **断点续传**: 支持暂停/恢复，断点自动续传
- **批量下载**: 选集批量下载，全选/取消全选
- **下载限速**: 自定义速度限制 (KB/s)
- **实时进度**: 速度、剩余时间、进度百分比实时显示
- **下载统计**: 下载中/已暂停/已完成/失败数量统计
- **文件管理**: 打开下载文件夹，清除已完成，删除记录

### 系统集成
- **系统托盘**: 最小化到托盘，后台运行，右键菜单
- **开机自启**: Windows 注册表 / macOS LaunchAgent / Linux .desktop
- **窗口置顶**: 一键置顶窗口
- **窗口记忆**: 保存/恢复窗口位置和大小
- **剪贴板**: 获取/设置剪贴板内容
- **配置导入导出**: 备份配置地址、直播源、收藏、设置

### 爬虫引擎
- **Type0 (API)**: 完全支持，CMS 苹果接口规范，请求重试/Cookie管理
- **Type4 (JS)**: 内置 QuickJS 引擎，无需外部 Node.js
- **Type1 (JAR)**: 不支持 (桌面端无法运行 Dalvik)
- **视频解析**: JSON接口/嗅探/JSON_V2 三种模式
- **请求增强**: 自动重试、超时处理、429限流处理、JSON提取容错

### 界面设计
- **暗色/浅色主题**: 一键切换，跟随系统
- **渐变设计**: 主题色渐变，侧边栏渐变背景
- **毛玻璃效果**: 工具栏、弹窗、Toast 毛玻璃背景
- **动画过渡**: 视图切换、弹窗、卡片、Toast 平滑动画
- **骨架屏**: 加载时显示 shimmer 动画骨架
- **网格密度**: 标准/紧凑/大图三种模式
- **响应式布局**: 自适应窗口大小

## 快捷键

| 按键 | 功能 |
|------|------|
| 空格 | 播放/暂停 |
| ← / → | 快退/快进 10秒 |
| ↑ / ↓ | 音量增减 |
| F | 全屏切换 |
| N | 下一集 |
| P | 上一集 |
| M | 静音切换 |
| D | 弹幕开关 |
| R | 画面比例切换 |
| S | 截图 |
| Esc | 退出全屏/关闭播放器 |
| 双击 | 全屏切换 |
| 滚轮 | 音量调节 |
| 右键 | 播放器菜单 |

## 打包为 EXE

### 环境要求
- Python 3.8+
- Windows 10/11 (推荐，需 WebView2 运行时)
- 或 Linux/macOS (需对应 webview 后端)

### 一键打包
```bash
# Windows
双击运行 build.bat

# 或手动执行
pip install -r requirements.txt
pyinstaller build.spec --noconfirm
```

生成的 EXE 位于 `dist/TVBoxDesktop.exe`，单文件无依赖，可直接复制使用。
EXE 包含专业图标和版本信息，文件属性完整。

### 手动运行 (开发模式)
```bash
pip install -r requirements.txt
python main.py
```

## 项目结构

```
tvbox-desktop/
├── main.py              # 主入口，pywebview 窗口 + API 桥接 + 系统托盘
├── config.py            # TVBox 配置解析 (Site/LiveSource/Parse/VodItem)
├── spider.py            # 爬虫引擎 (ApiSpider/JsSpider/SpiderManager)
├── live.py              # 直播源解析 (M3U/TXT/JSON)
├── epg.py               # EPG 节目单解析 (XMLTV)
├── parser.py            # VIP 视频解析引擎
├── proxy.py             # 本地 HTTP 代理服务器
├── sniffer.py           # 媒体 URL 嗅探器
├── database.py          # SQLite 本地存储
├── downloader.py        # 多线程下载管理器
├── tray.py              # 系统托盘 + 开机自启
├── build.spec           # PyInstaller 打包配置
├── build.bat            # 一键打包脚本
├── version_info.txt     # EXE 版本信息
├── requirements.txt     # Python 依赖
├── static/
│   ├── index.html       # 应用 UI 结构
│   ├── style.css        # 暗色/浅色主题样式 (2500+ 行)
│   ├── app.js           # 前端完整逻辑 (3000+ 行)
│   ├── icon.ico         # 应用图标
│   └── icon.png         # 应用图标 PNG
└── README.md
```

## 数据存储

所有数据存储在用户目录:
- Windows: `%APPDATA%/TVBoxDesktop/tvbox.db`
- Linux/macOS: `~/.tvbox-desktop/tvbox.db`

下载文件存储在:
- Windows: `~/Downloads/TVBoxDesktop/`
- Linux/macOS: `~/Downloads/TVBoxDesktop/`

截图存储在:
- Windows: `~/Pictures/TVBoxDesktop/`
- Linux/macOS: `~/Pictures/TVBoxDesktop/`

## 技术栈

- **GUI**: pywebview + WebView2 (Windows) / GTK (Linux) / WebKit (macOS)
- **视频**: HTML5 Video + hls.js
- **JS引擎**: QuickJS (内置) / Node.js (回退)
- **数据库**: SQLite (WAL 模式)
- **网络**: requests + urllib
- **系统托盘**: pystray + Pillow
- **剪贴板**: clipboard
- **打包**: PyInstaller (单文件 EXE，含图标和版本信息)

## 配置格式

支持标准 TVBox / CatVod JSON 配置格式:
```json
{
  "sites": [
    {"key": "site1", "name": "站点1", "type": 0, "api": "https://..."},
    {"key": "site2", "name": "站点2", "type": 4, "api": "https://...", "ext": "..."}
  ],
  "lives": [
    {"name": "直播1", "type": 0, "url": "https://.../live.m3u", "epg": "https://.../epg.xml"}
  ],
  "parses": [
    {"name": "解析1", "type": 0, "url": "https://..."}
  ]
}
```

## 版本历史

- **v5.0** - 系统托盘、多线程下载、AB回放、循环播放、窗口管理、配置导入导出、UI深度打磨
- **v4.0** - 弹幕、字幕、截图、画面比例、右键菜单、站点管理
- **v3.0** - 下载管理、搜索历史、EPG、直播收藏
- **v2.0** - 直播功能、播放历史、收藏管理
- **v1.0** - 基础点播浏览和播放
