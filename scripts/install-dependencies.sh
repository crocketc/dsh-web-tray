#!/bin/bash
# DSH Web Tray - macOS/Linux 依赖安装
# 用法：bash scripts/install-dependencies.sh
set -euo pipefail

if [[ "$OSTYPE" == "darwin"* ]]; then
    # Homebrew Python 需额外装 tk，否则 tkinter 不可用（配置向导需要）
    if command -v brew >/dev/null 2>&1; then
        brew list python-tk >/dev/null 2>&1 || brew install python-tk
    fi
fi

pip3 install pystray psutil pillow

python3 -c "import pystray, psutil, PIL; print('dependencies OK')"
echo "Done."
