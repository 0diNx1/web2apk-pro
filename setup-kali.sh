#!/usr/bin/env bash
# Install Web2APK Pro for the current user (no sudo, no system changes).
#   ./setup-kali.sh            install into ~/.local/share/web2apk-pro and link ~/.local/bin/web2apk
#   ./setup-kali.sh --icons    also install Pillow (needed only for custom --icon files)
set -euo pipefail

PREFIX="${WEB2APK_PREFIX:-$HOME/.local/share/web2apk-pro}"
BIN_DIR="${WEB2APK_BIN_DIR:-$HOME/.local/bin}"
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
want_icons=0; [[ "${1:-}" == "--icons" ]] && want_icons=1

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mERROR\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -ne 0 ]] || die "Run as a normal user, not root."
command -v python3 >/dev/null || die "python3 missing. Run: sudo apt install python3 python3-venv"
python3 - <<'PY' || die "Python 3.9+ is required."
import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
python3 -c "import venv, ensurepip" 2>/dev/null || die "python3-venv missing. Run: sudo apt install python3-venv"

say "Creating virtual environment in $PREFIX/venv"
mkdir -p "$PREFIX"
python3 -m venv --system-site-packages "$PREFIX/venv"   # system-site-packages lets apt's python3-pil be reused
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
say "Installing web2apk-pro"
if [[ $want_icons -eq 1 ]]; then
  "$PREFIX/venv/bin/pip" install --quiet "$here[icons]"
else
  "$PREFIX/venv/bin/pip" install --quiet "$here"
fi

mkdir -p "$BIN_DIR"
ln -sfn "$PREFIX/venv/bin/web2apk" "$BIN_DIR/web2apk"
say "Linked $BIN_DIR/web2apk"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "NOTE: $BIN_DIR is not on your PATH. Add it:  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc" ;;
esac

say "Checking prerequisites (nothing is installed automatically)"
"$PREFIX/venv/bin/web2apk" doctor || {
  echo
  echo "Doctor reported problems. Typical fixes on Kali:"
  echo "  sudo apt install openjdk-21-jdk        # JDK + keytool"
  echo "  sdkmanager 'platforms;android-35' 'build-tools;35.0.0' 'platform-tools'"
  echo "Then re-run: web2apk doctor"
}
