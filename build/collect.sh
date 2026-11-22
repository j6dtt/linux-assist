#!/bin/sh
# Runs inside an Ubuntu build stage (22.04 or 24.04).
# Installs the admin packages with their man pages. collect.py then writes
# everything the final image needs under /out/<release>/.
set -eu
export DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
rel=$(. /etc/os-release; echo "$VERSION_ID")
log() { echo "[collect $rel] $*"; }

log "Updating the package lists"
apt-get update -qq
apt-get -y -qq upgrade >/dev/null

have=""; skipped=""
for p in $(grep -v '^#' /build/packages.txt); do
    if apt-cache show "$p" >/dev/null 2>&1; then
        have="$have $p"
    else
        skipped="$skipped $p"
    fi
done
[ -n "$skipped" ] && log "Not in this release, skipped:$skipped"
echo "$skipped" > /tmp/skipped.txt

# Ubuntu's Docker images skip man pages on install. Stop that first.
rm -f /etc/dpkg/dpkg.cfg.d/excludes
log "Installing $(echo $have | wc -w) packages"
apt-get install -y -qq --no-install-recommends $have >/dev/null

log "Reinstalling every package so the man pages the base image left out come back"
apt-get install -y -qq --reinstall \
    $(dpkg-query -W -f='${binary:Package} ') >/dev/null

# Ubuntu's Docker images also divert "man" to a stub that only prints
# "This system has been minimized". Put the real one back.
log "Restoring the real man command"
if dpkg-divert --list /usr/bin/man | grep -q .; then
    rm -f /usr/bin/man
    dpkg-divert --quiet --remove --rename /usr/bin/man
fi
if [ ! -x /usr/bin/man ] || grep -q minimized /usr/bin/man; then
    log "The real man command could not be restored. Stopping."
    exit 1
fi

log "Building the man page database"
mandb -c -q
