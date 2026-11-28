#!/usr/bin/env bash
# Install a single double-clickable Desktop launcher: Positive Impact Trading.app
# (custom AppIcon.icns). Removes any legacy Desktop .command duplicate.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP="${HOME}/Desktop"
STARTER="${REPO_ROOT}/scripts/start_trading_morning.sh"
APPLY="${REPO_ROOT}/scripts/apply_launcher_icon.sh"
LEGACY_COMMAND="${DESKTOP}/Positive Impact Trading.command"

chmod +x "$STARTER" \
  "${REPO_ROOT}/scripts/install_desktop_launcher.sh" \
  "$APPLY"

# Drop legacy Desktop .command if present (pre single-.app layout).
if [[ -e "$LEGACY_COMMAND" ]]; then
  rm -f "$LEGACY_COMMAND"
  echo "Removed legacy: ${LEGACY_COMMAND}"
fi

# Rebuild Desktop .app with AppIcon.icns + bundled Launch.command
"$APPLY"

echo
echo "Installed:"
echo "  ${DESKTOP}/Positive Impact Trading.app"
echo "Double-click the .app to start (Gateway if needed + cockpit + browser)."
echo "If macOS warns on first open: right-click → Open."
