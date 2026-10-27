#!/bin/zsh
# Offline launcher. Files on exFAT need no symlinks or macOS permissions.
set -eu
umask 077
SCRIPT_DIR="$(cd -- "${0:A:h}" && pwd -P)"
if [[ "$SCRIPT_DIR" == */Contents/MacOS ]]; then
  PACKAGE="$SCRIPT_DIR/../Resources"
else
  PACKAGE="$SCRIPT_DIR/Bundle"
fi
PACKAGE="$(cd -- "$PACKAGE" && pwd -P)"
ROLE="$(cat "$PACKAGE/role.txt")"
ARCH="$(/usr/bin/uname -m)"
VERSION="$(/usr/bin/sw_vers -productVersion)"
MAJOR="${VERSION%%.*}"
if (( MAJOR < 12 )); then
  /usr/bin/osascript -e 'display alert "NexSearch 1.6.6" message "Нужна macOS 12 Monterey или новее. Для MacBook Pro 2016 подойдёт macOS Monterey."'
  exit 1
fi
if [[ "$ARCH" != "x86_64" && "$ARCH" != "arm64" ]]; then
  /usr/bin/osascript -e 'display alert "NexSearch 1.6.6" message "Неизвестный процессор Mac."'
  exit 1
fi
BUILD="$(cat "$PACKAGE/build-id.txt")"
RUNTIME="$HOME/Library/Application Support/NexSearch/Runtimes/$BUILD-$ARCH-$ROLE"
LOG_DIR="$HOME/Library/Logs/NexSearch"
/bin/mkdir -p "$RUNTIME" "$LOG_DIR"
LOG="$LOG_DIR/$ROLE-launch.log"
exec >>"$LOG" 2>&1
printf '\nNexSearch 1.6.6 — %s — %s\n' "$ROLE" "$ARCH"
MOUNT=""
LOCK_OWNED=""
cleanup() {
  if [[ -n "$MOUNT" ]]; then /usr/bin/hdiutil detach "$MOUNT" -quiet || true; /bin/rmdir "$MOUNT" 2>/dev/null || true; fi
  if [[ -n "$LOCK_OWNED" ]]; then /bin/rmdir "$LOCK_OWNED" 2>/dev/null || true; fi
}
finish() {
  local code=$?
  cleanup
  if (( code != 0 )); then
    /usr/bin/osascript -e 'on run argv' -e 'display alert "NexSearch 1.6.6" message ("Не удалось запустить программу. Журнал: " & (item 1 of argv))' -e 'end run' "$LOG" || true
  fi
}
trap finish EXIT
if [[ ! -f "$RUNTIME/ready" ]]; then
  # A per-runtime lock prevents two simultaneous extractions.
  LOCK="$RUNTIME/install.lock"
  if /bin/mkdir "$LOCK" 2>/dev/null; then
    LOCK_OWNED="$LOCK"
    (cd -- "$PACKAGE" && /usr/bin/shasum -a 256 -c checksums.txt)
    PYARCHIVE="$PACKAGE/Runtimes/python-$ARCH.tar.gz"
    /usr/bin/tar -xzf "$PYARCHIVE" -C "$RUNTIME"
    PY="$RUNTIME/python/bin/python3.12"
    "$PY" "$PACKAGE/installer.py" "$PACKAGE" "$RUNTIME" "$ARCH"
    if [[ "$ROLE" == "host" ]]; then
      MOUNT="$(/usr/bin/mktemp -d /tmp/nexsearch-pg.XXXXXXXX)"
      /usr/bin/hdiutil attach "$PACKAGE/Runtimes/postgres-17.dmg" -readonly -nobrowse -noautoopen -mountpoint "$MOUNT" -quiet
      /usr/bin/ditto "$MOUNT/Postgres.app" "$RUNTIME/Postgres.app"
      /usr/bin/hdiutil detach "$MOUNT" -quiet
      /bin/rmdir "$MOUNT";MOUNT=""
    fi
    /usr/bin/touch "$RUNTIME/ready"
    /bin/rmdir "$LOCK"
    LOCK_OWNED=""
  else
    for attempt in {1..120}; do
      [[ -f "$RUNTIME/ready" ]] && break
      /bin/sleep 1
    done
    if [[ ! -f "$RUNTIME/ready" ]]; then
      /usr/bin/osascript -e 'display alert "NexSearch 1.6.6" message "Подготовка первого запуска ещё выполняется. Повторите запуск через минуту."'
      exit 1
    fi
  fi
fi
export PYTHONPATH="$RUNTIME/dependencies:$RUNTIME/source"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONHOME
if [[ "$ROLE" == "host" ]]; then export NEXOL_POSTGRES_RUNTIME="$RUNTIME/Postgres.app/Contents/Versions/17"; fi
"$RUNTIME/python/bin/python3.12" "$RUNTIME/source/network/mac_entry.py" "$ROLE"
