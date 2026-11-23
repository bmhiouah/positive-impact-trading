#!/usr/bin/env bash
# Apply the trading-themed launcher icon to Desktop shortcuts.
# Direct resource-fork writes on Desktop often fail (Operation not permitted);
# NSWorkspace.setIcon works under $TMPDIR, then ditto preserves the fork.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ICON_PNG="${REPO_ROOT}/docs/assets/launcher-icon/launcher-icon-1024.png"
ICON_ICNS="${REPO_ROOT}/docs/assets/launcher-icon/PositiveImpactTrading.icns"
DESKTOP="${HOME}/Desktop"
COMMAND_TARGET="${DESKTOP}/Positive Impact Trading.command"
APP_TARGET="${DESKTOP}/Positive Impact Trading.app"

if [[ ! -f "$ICON_PNG" ]]; then
  echo "ERROR: missing icon PNG at ${ICON_PNG}" >&2
  exit 1
fi
if [[ ! -f "$ICON_ICNS" ]]; then
  echo "ERROR: missing icon icns at ${ICON_ICNS}" >&2
  exit 1
fi

set_icon_via_tmp() {
  local dest="$1"
  local tmp base
  tmp="$(mktemp -d)"
  base="$(basename "$dest")"
  if [[ -d "$dest" ]]; then
    ditto "$dest" "${tmp}/${base}"
  else
    cp "$dest" "${tmp}/${base}"
    chmod +x "${tmp}/${base}" 2>/dev/null || true
  fi

  local swift_file="${tmp}/seticon.swift"
  cat >"$swift_file" <<SWIFT
import AppKit
import Foundation
let imagePath = "${ICON_PNG}"
let targetPath = "${tmp}/${base}"
guard let image = NSImage(contentsOfFile: imagePath) else {
    fputs("failed to load \(imagePath)\n", stderr)
    exit(1)
}
let ok = NSWorkspace.shared.setIcon(image, forFile: targetPath, options: [])
if !ok {
    fputs("NSWorkspace.setIcon failed for \(targetPath)\n", stderr)
    exit(1)
}
print("setIcon ok")
SWIFT
  /usr/bin/swift "$swift_file"

  if [[ -d "$dest" ]]; then
    rm -rf "$dest"
  fi
  ditto "${tmp}/${base}" "$dest"
  /usr/bin/SetFile -a C "$dest" 2>/dev/null || true
  rm -rf "$tmp"
  echo "icon applied: ${dest}"
}

install_app_bundle() {
  local app="$1"
  local starter="${REPO_ROOT}/scripts/start_trading_morning.sh"
  rm -rf "$app"
  mkdir -p "${app}/Contents/MacOS" "${app}/Contents/Resources"
  cp "$ICON_ICNS" "${app}/Contents/Resources/AppIcon.icns"
  cat >"${app}/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Positive Impact Trading</string>
  <key>CFBundleDisplayName</key>
  <string>Positive Impact Trading</string>
  <key>CFBundleIdentifier</key>
  <string>com.positiveimpact.trading.launcher</string>
  <key>CFBundleVersion</key>
  <string>1.0</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleExecutable</key>
  <string>PositiveImpactTrading</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST
  # Open Terminal so Gateway login prompts / status stay visible (same as .command).
  cat >"${app}/Contents/MacOS/PositiveImpactTrading" <<EXEC
#!/bin/bash
osascript <<'OSA'
tell application "Terminal"
  activate
  do script "exec '${starter}'"
end tell
OSA
EXEC
  chmod +x "${app}/Contents/MacOS/PositiveImpactTrading"
  xattr -cr "$app" 2>/dev/null || true
  set_icon_via_tmp "$app"
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$app" >/dev/null 2>&1 || true
  echo "app installed: ${app}"
}

if [[ -e "$COMMAND_TARGET" ]]; then
  set_icon_via_tmp "$COMMAND_TARGET"
else
  echo "note: ${COMMAND_TARGET} not found — run install_desktop_launcher.sh first"
fi

install_app_bundle "$APP_TARGET"
