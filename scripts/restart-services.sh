#!/usr/bin/env bash
# Restart the Vite dev server + Playground sidecar for this project.
#
# What it does:
#   1. Finds any process currently listening on :8899 (sidecar) or :5173 (Vite)
#      and kills it with taskkill.
#   2. Launches each service in its own new terminal window so you can see
#      its logs live. Windows stay open after the service exits (cmd /k).
#
# Run from git-bash (or any bash shell on Windows that can call cmd):
#   bash scripts/restart-services.sh
#
# Or just double-click scripts/restart-services.bat (wraps this script
# identically for users who prefer Windows-native).

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SIDECAR_PORT=8899
VITE_PORT=5173

printf '[restart] project: %s\n\n' "$REPO_ROOT"

# ---- stop any process listening on a port ------------------------------
kill_port() {
    local port="$1"
    local label="$2"
    # netstat -ano | LISTENING rows: Proto  LocalAddr  ForeignAddr  State  PID
    # Match ":<port> " (with trailing space) against LocalAddr so we don't
    # catch reports on a larger port number (e.g. :5173 matching :51730).
    local pid
    pid=$(netstat -ano 2>/dev/null \
            | awk -v p=":${port}$" 'toupper($0) ~ /LISTENING/ {
                  split($2, a, ":"); if (a[length(a)] == '"$port"') print $NF
              }' \
            | head -1)
    if [ -n "$pid" ]; then
        printf '  stopping %s (PID %s)\n' "$label" "$pid"
        taskkill //PID "$pid" //F >/dev/null 2>&1 || true
    else
        printf '  %s: not running\n' "$label"
    fi
}

printf '=== stopping services ===\n'
kill_port "$SIDECAR_PORT" "Playground sidecar :$SIDECAR_PORT"
kill_port "$VITE_PORT"    "Vite dev server :$VITE_PORT"

# Give the OS a moment to release the port bindings before we relaunch.
sleep 2

# ---- start each in a new terminal window -------------------------------
# Use Windows paths for cmd.exe. `cygpath -w` handles git-bash's /d/dev -> D:\dev.
REPO_WIN="$(cygpath -w "$REPO_ROOT" 2>/dev/null || printf '%s' "$REPO_ROOT")"

printf '\n=== starting services ===\n'

# `cmd //c start ...` opens a new window and returns control to us.
# `/D` sets the working directory; `cmd /k` keeps the new window open so
# errors stay visible.
cmd //c start "Playground Sidecar :$SIDECAR_PORT" /D "$REPO_WIN" \
    cmd /k "python playground_server.py"
printf '  launched: Playground Sidecar  (new window)\n'

cmd //c start "Vite Dev Server :$VITE_PORT" /D "$REPO_WIN" \
    cmd /k "npm run dev"
printf '  launched: Vite Dev Server    (new window)\n'

printf '\n[restart] done.\n'
printf '  Sidecar health: http://127.0.0.1:%s/playground_api/health\n' "$SIDECAR_PORT"
printf '  UI:             http://localhost:%s\n' "$VITE_PORT"
