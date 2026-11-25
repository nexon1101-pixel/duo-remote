#!/usr/bin/env bash
# Update a DuoRemote server to the latest main version, headlessly:
# copy apply-update.ps1 to the target machine over SSH and run it there;
# it drives that machine's own admin-panel update API (127.0.0.1 + panel.lock).
#
#   usage: update-server.sh <ssh-alias> [version]
#
# Examples:
#   update-server.sh duo-pc          # the main PC
#   update-server.sh duo-ui          # Ui's CF-SV9 (once its SSH alias exists)
# No version = whatever the panel's update check reports as latest.
# Already at the target version is a no-op (exit 0).
# The remote login shell is expected to be PowerShell (as on the main PC).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALIAS="${1:?usage: update-server.sh <ssh-alias> [version]}"
VERSION="${2:-}"
SSH=(ssh -F /home/hatch/.ssh/config -o BatchMode=yes "$ALIAS")
SCP=(scp -F /home/hatch/.ssh/config)

"${SSH[@]}" 'powershell -NoProfile -Command "New-Item -ItemType Directory -Force -Path $env:USERPROFILE\Downloads\DuoRemote | Out-Null"'
"${SCP[@]}" "$DIR/apply-update.ps1" "$ALIAS:Downloads/DuoRemote/apply-update.ps1" >/dev/null
if [ -n "$VERSION" ]; then
  "${SSH[@]}" "powershell -NoProfile -ExecutionPolicy Bypass -File \"\$env:USERPROFILE\\Downloads\\DuoRemote\\apply-update.ps1\" $VERSION"
else
  "${SSH[@]}" 'powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\DuoRemote\apply-update.ps1"'
fi
