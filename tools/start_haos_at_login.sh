#!/bin/sh
# LaunchAgent entry point: start one existing VM after the user logs in.
# Deliberately no watchdog: an intentional stop stays stopped during this login.
set -eu

vm_id=${1:?Pass the existing UTM VM UUID or exact name}
utm_cli=/Applications/UTM.app/Contents/MacOS/utmctl
test -x "$utm_cli"
/usr/bin/open -g -j -a /Applications/UTM.app

attempt=0
while [ "$attempt" -lt 10 ]; do
    state=$("$utm_cli" status "$vm_id" 2>/dev/null || true)
    case "$state" in
        started|paused|suspended)
            exit 0
            ;;
        stopped)
            if "$utm_cli" start --hide "$vm_id"; then
                exit 0
            fi
            ;;
    esac
    attempt=$((attempt + 1))
    /bin/sleep 5
done
printf '%s\n' 'UTM login start failed; inspect the VM in UTM.' >&2
exit 1
