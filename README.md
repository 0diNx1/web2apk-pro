# Web2APK Pro

Turn a plain HTML/CSS/JS folder into an Android APK from the command line. No Android Studio, no Node, no framework.
The app is a small Kotlin WebView shell that serves your files from the APK's assets.

Built for Kali Linux (Bash + Python 3).

```bash
web2apk init my-app
web2apk build my-app
adb install -r my-app/.web2apk/out/my-app-1.0.0-debug.apk
```

## Status

- Python CLI (config, validation, asset handling, signing flow, dry-run, clean): unit and integration tests pass (`pytest`, 100 tests).
- The full chain Gradle -> APK -> device has **not been confirmed yet** on a real SDK. The generated Kotlin/Gradle project is written for AGP 8.7.3, Gradle 8.14.3, JDK 21, Kotlin 2.0.21, but treat your first build as the real test. If it breaks, open an issue with the log from `.web2apk/logs/`.
- The AGP-to-Gradle compatibility table in `builder.py` and the androidx versions in the template are taken from release notes and may need bumping.

## Requirements

| Need | Notes |
|---|---|
| Python 3.9+ | `python3-venv` for the installer |
| JDK 17+ (21 recommended) | `sudo apt install openjdk-21-jdk` (full JDK, needs `keytool`) |
| Android SDK | auto-detected: `/opt/android-sdk`, `$ANDROID_HOME`, `$ANDROID_SDK_ROOT`, `~/Android/Sdk` |
| SDK packages | `platforms;android-35`, `build-tools;35.0.0` (apksigner + zipalign); `platform-tools` for adb |
| Gradle 8.9 - 8.x | system Gradle is used if compatible, otherwise a wrapper is generated |
| Pillow (optional) | only for custom `--icon` files |
| Network | first build downloads AGP/androidx from Google Maven and Maven Central |

Missing SDK pieces:
```bash
sdkmanager --sdk_root=/opt/android-sdk "platforms;android-35" "build-tools;35.0.0" "platform-tools"
sdkmanager --sdk_root=/opt/android-sdk --licenses
```

## Install

```bash
tar xzf web2apk-pro.tar.gz && cd web2apk-pro
./setup-kali.sh            # venv in ~/.local/share/web2apk-pro, link in ~/.local/bin, no sudo
./setup-kali.sh --icons    # same, plus Pillow
web2apk doctor
```
Run without installing: `./web2apk --help` from the checkout.

Uninstall (removes only what the installer created):
```bash
./uninstall.sh             # venv + launcher link
./uninstall.sh --purge     # also ~/.config/web2apk and ~/.cache/web2apk
web2apk clean ./my-app --all --logs    # per-project build folders
```

## Usage

```bash
web2apk init DIR [--name N] [--package com.x.y]
web2apk build [DIR] [options]
web2apk doctor
web2apk config [show|get|set|unset|path]
web2apk keystore create PATH --alias A [--dname "CN=..."]
web2apk verify app.apk
web2apk clean [DIR] [--logs] [--all] [--dry-run]
web2apk version
```

Common build options:
```bash
web2apk build ./site --name "My App" --package com.me.myapp
web2apk build ./site --icon icon.png --min-sdk 23 --target-sdk 35
web2apk build ./site --entry pages/home.html --web-root public
web2apk build ./site --release --keystore ~/keys/release.p12 --key-alias mykey
web2apk build ./site --dry-run          # show the plan, write nothing
```
Every key from `web2apk.json` also has a flag (`--[no-]fullscreen`, `--[no-]internet`, `--[no-]js-bridge`, `--orientation`, ...). See `web2apk build --help`.

Flags: `-q` quiet, `-v` verbose (live Gradle output), `--no-color`, `--no-interaction` (never prompt), `--offline` (Gradle cache only), `--force` (overwrite a release APK), `--output-dir`, `--gradle-mode auto|system|wrapper`.

Output goes to `<project>/.web2apk/out/<app>-<version>-<debug|release>.apk`. Status text goes to stderr; on success stdout is just the APK path:
```bash
apk=$(web2apk build ./site -q --no-interaction)
```

Exit codes: `0` ok, `1` unexpected, `2` usage, `3` config/project, `4` toolchain, `5` build, `6` signing, `7` needed a prompt but `--no-interaction` was set.

## Project config

Put `web2apk.json` next to your `index.html` (it is not packaged into the APK):

```json
{
  "app_name": "My App",
  "package_id": "com.me.myapp",
  "version_name": "1.0.0",
  "version_code": 1,
  "entry_point": "index.html",
  "web_root": "auto",
  "min_sdk": 23,
  "target_sdk": 35,
  "orientation": "portrait",
  "internet_access": false,
  "allow_cleartext": false,
  "allowed_origins": [],
  "fullscreen": false,
  "allow_file_uploads": true,
  "allow_downloads": false,
  "permissions": [],
  "js_bridge": false,
  "debug_webview": false,
  "status_bar_color": "#1F2937",
  "splash_color": "#111827",
  "icon": "icon.png"
}
```

Order of precedence: command line > `web2apk.json` > global defaults (`web2apk config set defaults.orientation portrait`) > built-ins. Unknown keys and wrong types are errors.

| Key | Meaning |
|---|---|
| `web_root`, `entry_point` | `auto` looks for `index.html` in `.`, `public/`, `assets/`, `www/`. Set explicitly for anything else. |
| `internet_access` | `false` = no INTERNET permission, fully offline app. |
| `allowed_origins` | Hosts (`example.com`, `*.example.com`) allowed to load inside the app. Needs `internet_access`. Other links open in the browser. |
| `allow_cleartext`, `allow_downloads` | Need `internet_access`. Off by default. |
| `permissions` | Any of `camera`, `microphone`, `location`. Only listed ones are declared. |
| `orientation` | `auto`, `portrait`, `landscape`. |
| `js_bridge` | Enables `window.Web2ApkBridge` (see below). |
| `debug_webview` | `chrome://inspect` support, debug builds only. |
| `icon` | PNG/JPEG/WebP, needs Pillow. Without it a plain placeholder icon is generated. |

Add `<meta name="viewport" content="width=device-width, initial-scale=1">` to your pages; the tool warns if it's missing.

## Debug vs release

- **Debug**: signed with the shared Android debug key. For your own devices and testing.
- **Release**: signed with your key, not debuggable. That key is the app's identity; lose it and you can't ship updates.

```bash
web2apk keystore create ~/keys/release.p12 --alias mykey --dname "CN=Your Name, O=You, C=US"
web2apk build ./site --release --keystore ~/keys/release.p12 --key-alias mykey
web2apk verify ./site/.web2apk/out/site-1.0.0-release.apk
```

For CI, pass secrets through the environment:
```bash
export WEB2APK_KEYSTORE=~/keys/release.p12
export WEB2APK_KEY_ALIAS=mykey
export WEB2APK_KEYSTORE_PASSWORD=...        # set from your secret store, not typed inline
web2apk build ./site --release --no-interaction -q
```

Signing runs after Gradle (`zipalign` -> `apksigner sign` -> `apksigner verify`). Passwords reach those tools only through child-process env vars. They are not on command lines, in logs, or in generated files. Existing keystores and release APKs are never overwritten.

## Install on a phone

1. Phone: Developer options -> USB debugging on, plug in, accept the prompt.
2. ```bash
   adb devices
   adb install -r path/to/app-debug.apk
   adb shell monkey -p com.me.myapp 1
   ```
3. `INSTALL_FAILED_UPDATE_INCOMPATIBLE` means a different signing key is already installed: `adb uninstall com.me.myapp`.

No adb: copy the APK to the phone and allow "install unknown apps" for your file manager.

## How it works

```
check toolchain -> read web project -> generate Android project -> Gradle -> (release: align/sign/verify) -> APK
```

- Generated project lives in `<project>/.web2apk/build/android` and is updated incrementally.
- Pages load from `https://appassets.androidplatform.net/assets/www/...` via `WebViewAssetLoader`. No `file://`.
- **Gradle wrapper**: a global Gradle only works if it matches what the Android Gradle Plugin supports. `auto` uses your system Gradle when compatible, otherwise generates a wrapper (via `gradle wrapper` in a scratch dir, cached in `~/.cache/web2apk/`) and copies it in. The wrapper downloads its Gradle distribution on first run.
- Pins (override with `web2apk config set ...`): AGP 8.7.3, Kotlin 2.0.21, compileSdk 35, androidx appcompat 1.7.0 / webkit 1.11.0 / core-splashscreen 1.0.1. `targetSdk` is independent of compileSdk.

```
src/web2apk/   cli  builder  config  project  android_sdk  signing  validation  diagnostics  utils
               templates/android/   Kotlin WebView app + Gradle Kotlin DSL (*.tpl)
               templates/starter/   used by `init`
tests/   examples/hello-web/   setup-kali.sh   uninstall.sh   web2apk (launcher)
```

## Security notes

Build time:
- Project JS and shell scripts are never executed, only text-scanned.
- Asset paths must stay inside the web root. `..`, absolute paths and symlink escapes are rejected; symlinks are skipped.
- Not packaged: dotfiles (`.env`, `.git`), `node_modules`, `*.keystore *.jks *.p12 *.pem *.key`, `web2apk.json`.
- `clean` only deletes a marker-checked folder under `.web2apk`. Nothing uses sudo.

Runtime:
- JavaScript is on (your app needs it), so any script in your pages runs with WebView privileges. Don't include untrusted third-party scripts.
- No file access, mixed content blocked, cleartext off by default, TLS errors never bypassed, `allowBackup=false`.
- Navigation is limited to the app origin plus `allowed_origins`. Other http(s)/mailto/tel links go to the system browser; other schemes are dropped.
- `internet_access: true` lets your pages make network requests. `allowed_origins` restricts navigation, not subresources; use a Content-Security-Policy meta tag for those.
- Camera/mic/location are only granted when listed in `permissions` and only to trusted origins.
- Downloads need a confirmation dialog, https only.

### JS bridge

With `"js_bridge": true`, pages from the app's own origin get `window.Web2ApkBridge` (via `addWebMessageListener`, not `addJavascriptInterface`, so remote frames can't reach it). Messages are JSON strings:

```js
Web2ApkBridge.addEventListener("message", e => console.log(JSON.parse(e.data)));
Web2ApkBridge.postMessage(JSON.stringify({ id: 1, method: "showToast", args: { text: "hi" } }));
```
Methods: `getAppInfo`, `isOnline`, `showToast` (1-200 chars). Anything else is rejected. Full example in `examples/hello-web/script.js`.

## Known limits

No `<input capture>`, `blob:` downloads, push, or deep links. Custom icons aren't adaptive. Minify/R8 and release lint are off. Link-level allow-listing only (see above).

## Troubleshooting

Start with `web2apk doctor`. Logs: `<project>/.web2apk/logs/` (last 20 kept). Details and fixes for Java, Gradle, SDK, signing and install errors: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
pytest
```

## License

MIT, see `LICENSE`.
