#!/bin/bash
# DSH Web Tray - 跨平台打包入口
# 用法：bash scripts/build.sh
set -euo pipefail

echo "Building DSH Web Tray..."

pip install pyinstaller pystray psutil pillow

if [[ "$OSTYPE" == "darwin"* ]]; then
    bash "$(dirname "$0")/build-app.sh"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" || "$OSTYPE" == "cygwin" ]]; then
    powershell -ExecutionPolicy Bypass -File "$(dirname "$0")/build-exe.ps1"
else
    echo "Unsupported platform: $OSTYPE（Linux 打包为可选项，未实现）"
    exit 1
fi
