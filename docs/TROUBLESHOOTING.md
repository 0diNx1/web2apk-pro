# Troubleshooting

Always start with `web2apk doctor`. Build logs: `<project>/.web2apk/logs/build-*.log` (last 20 kept). Re-run with `-v` for live Gradle output.

## Java
| Symptom | Fix |
|---|---|
| `Java (JDK) not found` / `keytool` missing | `sudo apt install openjdk-21-jdk` (full JDK, not the JRE) |
| `Unsupported class file major version`, `requires Java 17` | AGP 8.x needs JDK 17+. `sudo update-alternatives --config java`, or `web2apk config set java_home /usr/lib/jvm/java-21-openjdk-amd64` |
| Wrong JDK picked | order: config `java_home` > `$JAVA_HOME` > `java` on PATH > `/usr/lib/jvm/*` (first that is >= 17) |

## Gradle
| Symptom | Fix |
|---|---|
| `Minimum supported Gradle version is X` | system Gradle too old for AGP: `--gradle-mode wrapper` (bootstrap needs any installed Gradle once) |
| AGP/Gradle mismatch after changing `agp_version` | `web2apk config set gradle_version <ver>` and use `--gradle-mode wrapper`; see the table in `builder.py` (`AGP_MIN_GRADLE`) |
| `Could not resolve ...` / `UnknownHostException` / PKIX | first build needs Google Maven + Maven Central: check network, proxy (`~/.gradle/gradle.properties`: `systemProp.https.proxyHost=...`), system clock. Afterwards `--offline` works |
| Wrapper download fails | allow `services.gradle.org`, or use `--gradle-mode system` |
| Out of memory | raise `org.gradle.jvmargs` in `templates/android/gradle.properties.tpl` |
| Stale daemon weirdness | `gradle --stop`, then `web2apk clean . && web2apk build .` |

## Android SDK
| Symptom | Fix |
|---|---|
| `Android SDK not found` | `web2apk config set sdk_path /opt/android-sdk` (also reads `ANDROID_HOME`/`ANDROID_SDK_ROOT`) |
| `SDK platform android-35 missing` | `sdkmanager --sdk_root=/opt/android-sdk "platforms;android-35"` |
| `No usable build-tools` | `sdkmanager "build-tools;35.0.0"` (needs `apksigner` and `zipalign`) |
| `licences not accepted` | `sdkmanager --sdk_root=/opt/android-sdk --licenses` |
| `Failed to install the following Android SDK packages` while SDK is root-owned | AGP can't write into it. Install the needed packages yourself (as the SDK owner), or use an SDK dir you own: `web2apk config set sdk_path ~/Android/Sdk` |
| `Failed to find target with hash string 'android-35'` | platform 35 missing, see above (compileSdk is always 35) |
| `AAPT2 ... failed to start` | 64-bit libs present? `/tmp` mounted noexec? reinstall build-tools |

## Signing
| Symptom | Fix |
|---|---|
| `No keystore configured` | `web2apk keystore create ~/keys/release.p12 --alias mykey`, then `--keystore` / `WEB2APK_KEYSTORE` / `web2apk config set keystore PATH` |
| `Keystore password required` (exit 7) | `--no-interaction` needs `WEB2APK_KEYSTORE_PASSWORD` (and `WEB2APK_KEY_ALIAS`) |
| `Keystore was tampered with, or password was incorrect` | wrong password; PKCS12 keystores have one password. For a legacy JKS with a separate key password set `WEB2APK_KEY_PASSWORD` |
| `Failed to load signer` / alias not found | `keytool -list -keystore FILE` to see aliases |
| `Refusing to overwrite existing release APK` | bump `version_name`/`version_code`, change `--output-dir`, or `--force` |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` on `adb install` | installed copy was signed with a different key (e.g. debug vs release): `adb uninstall <package>` first |
| `INSTALL_FAILED_NO_MATCHING_ABIS`/`MIN_SDK` | device older than `min_sdk`; lower `--min-sdk` (>= 21) |
| `INSTALL_PARSE_FAILED_NO_CERTIFICATES` | installing an unsigned APK; use the files from `.web2apk/out/` |

## App behaviour
| Symptom | Fix |
|---|---|
| Blank page | build with `--debug-webview`, `chrome://inspect`; check relative paths (case-sensitive!) and the entry point |
| Page looks like a desktop site | add the viewport meta tag |
| CDN scripts/fonts don't load | `internet_access` is false by default; enable it, or vendor the files into the project |
| `http://` resources blocked | use https, or `allow_cleartext` (requires `internet_access`) |
| `fetch()`/ES modules from local files fail |so module imports and fetch of relative files work; `file://` assumptions don't |
| Link opens in the browser | host not in `allowed_origins` (by design) |
| Bridge object missing | `js_bridge: true`; only pages from the app's own origin get it |
