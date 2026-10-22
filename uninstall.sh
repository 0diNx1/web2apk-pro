#!/usr/bin/env bash
# Remove ONLY Web2APK Pro files. Never touches your SDK, Java, Gradle, projects or keystores.
#   ./uninstall.sh            remove the program (venv + launcher link)
#   ./uninstall.sh --purge    also remove ~/.config/web2apk and ~/.cache/web2apk
set -euo pipefail
PREFIX="${WEB2APK_PREFIX:-$HOME/.local/share/web2apk-pro}"
BIN_DIR="${WEB2APK_BIN_DIR:-$HOME/.local/bin}"
link="$BIN_DIR/web2apk"

if [[ -L "$link" && "$(readlink "$link")" == "$PREFIX/venv/bin/web2apk" ]]; then rm -v "$link"
elif [[ -e "$link" ]]; then echo "Leaving $link alone (not created by this installer)."; fi

if [[ -f "$PREFIX/venv/pyvenv.cfg" && "$PREFIX" == */web2apk-pro ]]; then rm -rv "$PREFIX"
else echo "No installation found at $PREFIX"; fi

if [[ "${1:-}" == "--purge" ]]; then
  rm -rfv "${XDG_CONFIG_HOME:-$HOME/.config}/web2apk" "${XDG_CACHE_HOME:-$HOME/.cache}/web2apk"
fi
echo "Done. Per-project build folders (<project>/.web2apk) are removed with: web2apk clean <project> --all --logs"
