#!/bin/bash
# DSH Web Tray - macOS 打包（.app + ad-hoc 签名 + DMG）
# 用法：bash scripts/build-app.sh
set -euo pipefail
cd "$(dirname "$0")/.."

pip3 install pyinstaller

# 图标：icon.png → iconset → icns（iconutil 为 macOS 自带）
if [[ ! -f resources/icon.icns && -f resources/icon.png ]]; then
    mkdir -p icon.iconset
    for size in 16 32 128 256 512; do
        sips -z $size $size resources/icon.png --out "icon.iconset/icon_${size}x${size}.png" >/dev/null
        sips -z $((size * 2)) $((size * 2)) resources/icon.png --out "icon.iconset/icon_${size}x${size}@2x.png" >/dev/null
    done
    iconutil -c icns icon.iconset
    mkdir -p resources
    mv icon.icns resources/icon.icns
    rm -rf icon.iconset
fi

ICON_ARGS=()
if [[ -f resources/icon.icns ]]; then
    ICON_ARGS=(--icon=resources/icon.icns)
fi

pyinstaller \
    --name="DSH Web Tray" \
    --windowed \
    --onefile \
    --osx-bundle-identifier=com.dsh.webtray \
    --add-data="resources:resources" \
    "${ICON_ARGS[@]}" \
    dsh-web-tray.py

# 必做：ad-hoc 签名（未签名 .app 会被 Gatekeeper 判为"已损坏"）
codesign --force --deep --sign - "dist/DSH Web Tray.app"

# LSUIElement：纯菜单栏应用，不出现在 Dock（签名后改 plist 需重签）
PLIST="dist/DSH Web Tray.app/Contents/Info.plist"
if [[ -f "$PLIST" ]]; then
    /usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null || \
        /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$PLIST"
    /usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$PLIST" 2>/dev/null || \
        /usr/libexec/PlistBuddy -c "Set :NSHighResolutionCapable true" "$PLIST"
    codesign --force --deep --sign - "dist/DSH Web Tray.app"
fi

hdiutil create -volname "DSH Web Tray" \
    -srcfolder "dist/DSH Web Tray.app" \
    -ov -format UDZO "dsh-web-tray-$(uname -m).dmg"

echo "Build complete: dsh-web-tray-$(uname -m).dmg"
echo "注意：首次打开可能需要右键→打开（未公证的 ad-hoc 签名）。"
