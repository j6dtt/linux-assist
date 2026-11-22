#!/bin/sh
# Runs inside an Ubuntu build stage, after collect.sh.
# Docker's own CLI and Compose (download.docker.com) and kubectl (dl.k8s.io),
# at the pinned versions in the Dockerfile, so their man pages and --help
# text can be collected. Like every package here, none of it reaches the
# final image: only the text collect.py and helpman.py write.
set -eu
: "${DOCKER_VERSION:?}" "${COMPOSE_VERSION:?}" "${KUBECTL_VERSION:?}"
. /etc/os-release
rel=$VERSION_ID
code=$VERSION_CODENAME
arch=$(dpkg --print-architecture)
log() { echo "[collect $rel] $*"; }

# Docker's release signing key, checked against its published fingerprint.
DOCKER_KEY_FPR=9DC858229FC7DD38854AE2D88D81803C0EBFCD88
log "Adding Docker's package source (signing key checked)"
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /tmp/docker.asc
fpr=$(gpg --show-keys --with-colons /tmp/docker.asc 2>/dev/null | awk -F: '/^fpr/{print $10; exit}')
if [ "$fpr" != "$DOCKER_KEY_FPR" ]; then
    log "Docker's key has fingerprint '$fpr', expected $DOCKER_KEY_FPR. Stopping."
    exit 1
fi
install -d -m 0755 /etc/apt/keyrings
gpg --dearmor < /tmp/docker.asc > /etc/apt/keyrings/docker.gpg
echo "deb [arch=$arch signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $code stable" \
    > /etc/apt/sources.list.d/docker.list
apt-get update -qq

log "Installing docker-ce-cli $DOCKER_VERSION and docker-compose-plugin $COMPOSE_VERSION"
apt-get install -y -qq --no-install-recommends \
    "docker-ce-cli=5:$DOCKER_VERSION-1~ubuntu.$rel~$code" \
    "docker-compose-plugin=$COMPOSE_VERSION-1~ubuntu.$rel~$code" >/dev/null

log "Downloading kubectl $KUBECTL_VERSION (checksum checked)"
base="https://dl.k8s.io/release/v$KUBECTL_VERSION/bin/linux/$arch"
curl -fsSL "$base/kubectl" -o /usr/local/bin/kubectl
sum=$(curl -fsSL "$base/kubectl.sha256")
if ! echo "$sum  /usr/local/bin/kubectl" | sha256sum -c --quiet; then
    log "kubectl's checksum does not match dl.k8s.io's. Stopping."
    exit 1
fi
chmod 755 /usr/local/bin/kubectl

log "Rebuilding the man page database"
mandb -c -q
