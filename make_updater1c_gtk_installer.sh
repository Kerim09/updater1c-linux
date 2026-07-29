#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(<"$ROOT/VERSION")"
grep -Fq "APP_VERSION = \"$VERSION\"" "$ROOT/gtk_port/updater1c_gtk.py" || { echo "ОШИБКА: APP_VERSION не совпадает с VERSION ($VERSION)" >&2; exit 1; }
OUT="$ROOT/dist/updater1c-linux-${VERSION}.run"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/stage/opt/updater1c-linux" "$WORK/stage/usr/local/bin" \
  "$WORK/stage/usr/share/applications" "$WORK/stage/usr/share/icons/hicolor/256x256/apps"
cp -a "$ROOT/gtk_port" "$WORK/stage/opt/updater1c-linux/"
cp -a "$ROOT/core" "$WORK/stage/opt/updater1c-linux/"
cp "$ROOT/assets/updater1c.png" "$WORK/stage/opt/updater1c-linux/"
cp "$ROOT/assets/updater1c.png" "$WORK/stage/usr/share/icons/hicolor/256x256/apps/updater1c-linux.png"
cp "$ROOT/VERSION" "$WORK/stage/opt/updater1c-linux/"
cp "$ROOT/packaging/linux/updater1c-linux" "$WORK/stage/usr/local/bin/updater1c-linux"
cp "$ROOT/packaging/linux/io.github.kerim.updater1clinux.desktop" "$WORK/stage/usr/share/applications/io.github.kerim.updater1clinux.desktop"
chmod 755 "$WORK/stage/usr/local/bin/updater1c-linux"
find "$WORK/stage" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$WORK/stage" -type f \( -name '*.pyc' -o -name '*.bak' -o -name '*.before_*' -o -name 'main.py' \) -delete
[[ "$(find "$WORK/stage/usr/share/applications" -type f -name '*.desktop' | wc -l)" -eq 1 ]]
tar -C "$WORK/stage" -czf "$WORK/payload.tar.gz" .
mkdir -p "$ROOT/dist"
cat > "$OUT" <<'INSTALLER'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$(id -u)" -eq 0 ]] || exec sudo bash "$0" "$@"
APP_DIR=/opt/updater1c-linux; STAMP=$(date +%Y%m%d_%H%M%S); BACKUP="${APP_DIR}.backup-${STAMP}"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
[[ -d "$APP_DIR" ]] && mv "$APP_DIR" "$BACKUP"
for desktop_id in io.github.kerim.updater1clinux.desktop io.github.kerim1c.updater1clinux.desktop io.github.kerim1c.updater1clinux.gtk.desktop updater1c-linux.desktop updater1c-linux-gtk.desktop updater1c-gtk.desktop; do rm -f "/usr/share/applications/$desktop_id"; done
for f in /usr/local/bin/updater1c-gtk /usr/local/bin/updater1c-linux-gtk /opt/updater1c-linux/bin/updater1c-gtk-gui; do rm -f "$f"; done
LINE=$(awk '/^__PAYLOAD_BELOW__$/ {print NR+1; exit}' "$0")
tail -n +"$LINE" "$0" | tar -xz -C "$TMP"
mv "$TMP/opt/updater1c-linux" "$APP_DIR"
install -Dm755 "$TMP/usr/local/bin/updater1c-linux" /usr/local/bin/updater1c-linux
install -Dm644 "$TMP/usr/share/applications/io.github.kerim.updater1clinux.desktop" /usr/share/applications/io.github.kerim.updater1clinux.desktop
install -Dm644 "$TMP/usr/share/icons/hicolor/256x256/apps/updater1c-linux.png" /usr/share/icons/hicolor/256x256/apps/updater1c-linux.png
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor >/dev/null 2>&1 || true
if command -v kbuildsycoca6 >/dev/null 2>&1; then kbuildsycoca6 >/dev/null 2>&1 || true; elif command -v kbuildsycoca5 >/dev/null 2>&1; then kbuildsycoca5 >/dev/null 2>&1 || true; fi
echo "Установлено: Обновлятор 1С Linux $(<"$APP_DIR/VERSION")"
echo "Резервная копия программных файлов: $BACKUP"
exit 0
__PAYLOAD_BELOW__
INSTALLER
cat "$WORK/payload.tar.gz" >> "$OUT"
chmod 755 "$OUT"; sha256sum "$OUT" > "$OUT.sha256"
echo "$OUT"
