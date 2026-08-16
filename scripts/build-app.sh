#!/bin/bash
# DSH Web Tray - macOS 打包（.app + ad-hoc 签名 + DMG）
# 用法：bash scripts/build-app.sh
set -euo pipefail
cd "$(dirname "$0")/.."

APP_NAME="DSH Web Tray"
ARCH="$(uname -m)"

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

# onedir（非 onefile）：macOS 的 .app 本质就是目录 bundle，
# onefile 会先把整个程序解压到临时目录再跑，慢且被 Gatekeeper 视为可疑，
# PyInstaller v7 将直接禁止 onefile + windowed 的组合。
# 清理旧产物：build/ 中间目录、dist/ 下的 onedir 目录与 .app 均需清掉。
rm -rf "build/$APP_NAME" "dist/$APP_NAME" "dist/$APP_NAME.app"
pyinstaller \
    --name="$APP_NAME" \
    --windowed \
    --onedir \
    --osx-bundle-identifier=com.dsh.webtray \
    --add-data="resources:resources" \
    "${ICON_ARGS[@]}" \
    dsh-web-tray.py

# 必做：ad-hoc 签名（未签名 .app 会被 Gatekeeper 判为"已损坏"）
codesign --force --deep --sign - "dist/$APP_NAME.app"

# LSUIElement：纯菜单栏应用，不出现在 Dock（签名后改 plist 需重签）
PLIST="dist/$APP_NAME.app/Contents/Info.plist"
if [[ -f "$PLIST" ]]; then
    /usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null || \
        /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$PLIST"
    /usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$PLIST" 2>/dev/null || \
        /usr/libexec/PlistBuddy -c "Set :NSHighResolutionCapable true" "$PLIST"
    codesign --force --deep --sign - "dist/$APP_NAME.app"
fi

# DMG 布局：应用图标 + Applications 快捷方式（Finder 显示"应用程序"），
# 用户把应用拖到"应用程序"即完成安装——没有链接的话用户根本不知道往哪拖。
STAGE="dist/dmg-stage"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "dist/$APP_NAME.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

hdiutil create -volname "$APP_NAME" \
    -srcfolder "$STAGE" \
    -ov -format UDZO "dsh-web-tray-$ARCH.dmg"
rm -rf "$STAGE"

echo "Build complete: dsh-web-tray-$ARCH.dmg"
echo "打开 DMG 后把应用拖到『应用程序』即可安装。"
echo "注意：首次打开可能需要右键→打开（未公证的 ad-hoc 签名）。"
