# DSH Web Tray - Windows 打包（单文件 exe，无控制台窗口）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\build-exe.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

pip install pyinstaller --disable-pip-version-check

if (-not (Test-Path 'resources\icon.ico')) {
    python scripts\generate_icons.py
}

pyinstaller `
    --onefile `
    --noconsole `
    --name=dsh-web-tray `
    --icon=resources\icon.ico `
    --add-data='resources;resources' `
    dsh-web-tray.py

Write-Host "Build complete: dist\dsh-web-tray.exe"
