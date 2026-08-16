# DSH Web Tray - Windows 依赖安装
# 用法：powershell -ExecutionPolicy Bypass -File scripts\install-dependencies.ps1
param(
    [string]$Index = ""  # 可选镜像，如 https://pypi.tuna.tsinghua.edu.cn/simple
)
$ErrorActionPreference = 'Stop'

$args_ = @('install', 'pystray', 'psutil', 'pillow')
if ($Index) { $args_ += @('-i', $Index) }

Write-Host "Installing Python dependencies (pystray psutil pillow)..."
pip @args_
if ($LASTEXITCODE -ne 0) { Write-Error 'pip install failed'; exit 1 }

python -c "import pystray, psutil, PIL; print('dependencies OK')"
Write-Host 'Done.'
