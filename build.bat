@echo off
chcp 65001 >nul 2>&1
title TVBox Desktop EXE 打包工具
color 0B

echo ╔══════════════════════════════════════════╗
echo ║       TVBox Desktop EXE 打包工具         ║
echo ║          Version 5.0.0                   ║
echo ╚══════════════════════════════════════════╝
echo.

:: 检查 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python, 请先安装 Python 3.8+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 检查是否在项目目录
if not exist "main.py" (
    echo [错误] 请在项目根目录运行此脚本
    pause
    exit /b 1
)

echo [1/5] 检查依赖...
python -c "import webview" 2>nul
if %errorlevel% neq 0 (
    echo 正在安装依赖...
    pip install -r requirements.txt
)

:: 生成图标
if not exist "static\icon.ico" (
    echo [2/5] 生成图标...
    pip install Pillow --quiet 2>nul
    python -c "from PIL import Image, ImageDraw; img = Image.new('RGBA', (256, 256), (0,0,0,0)); d = ImageDraw.Draw(img); d.rounded_rectangle([26,26,230,230], radius=51, fill=(24,144,255,255)); d.polygon([(96,96),(96,160),(160,128)], fill=(255,255,255,255)); img.save('static/icon.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]); img.save('static/icon.png')"
) else (
    echo [2/5] 图标已存在, 跳过
)

echo [3/5] 清理旧文件...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "TVBoxDesktop.spec" del /q "TVBoxDesktop.spec"

echo [4/5] 开始打包 (可能需要几分钟)...
echo.

pyinstaller build.spec --noconfirm

if %errorlevel% neq 0 (
    echo.
    echo [错误] 打包失败! 请检查错误信息
    pause
    exit /b 1
)

echo.
echo [5/5] 打包完成!
echo.
echo ╔══════════════════════════════════════════╗
echo ║            打包成功!                     ║
echo ╠══════════════════════════════════════════╣
echo ║  输出文件: dist\TVBoxDesktop.exe        ║
echo ║  文件大小:                              ║
for %%I in (dist\TVBoxDesktop.exe) do echo ║  %%~zI bytes                          ║
echo ╚══════════════════════════════════════════╝
echo.
echo 可以直接运行 dist\TVBoxDesktop.exe
echo.
pause
