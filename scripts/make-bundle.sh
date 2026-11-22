#!/bin/sh
# Packs what an air-gapped server needs into dist/la-<version>.tar:
#   image.tar.gz  the image (docker load), built, tested and scanned first
#   compose.yaml  with the dev model removed and the prod Super block on
#   la            the wrapper
#   USAGE.txt     how to use la, every command
#   env.example   LLM_API_KEY, needed for GPT-5.4 only
#   INSTALL.txt, SHA256SUMS
# No secrets: .env stays behind.
set -eu
cd "$(dirname "$0")/.."

image=$(sed -n 's/^x-image: &image //p' compose.yaml)
version=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' src/la/__init__.py)
name="la-$version"
out="dist/$name"

# Build, test and scan: any failure stops here, before anything is packed.
scripts/build.sh "$image"

echo "Packing $name ..."
rm -rf "$out" "dist/$name.tar" "dist/$name.tar.sha256"
mkdir -p "$out"
docker save "$image" | gzip -6 > "$out/image.tar.gz"

# The dev model is online: it must not stay switched on in prod. Keep the
# prod Super block on (its host is a placeholder la ask names until filled
# in); the GPT-5.4 block stays commented out.
sed -e '/^  # Dev: NVIDIA/,/^  llm-model: &llm-model nvidia\/nemotron-3-ultra/d' \
    -e 's|^  # llm-url:   &llm-url http://<vllm-host>|  llm-url:   \&llm-url http://<vllm-host>|' \
    -e 's|^  # llm-model: &llm-model nvidia/nemotron-3-super|  llm-model: \&llm-model nvidia/nemotron-3-super|' \
    compose.yaml > "$out/compose.yaml"
if grep -q 'integrate.api.nvidia.com' "$out/compose.yaml"; then
    echo "The dev model is still in the bundle's compose.yaml: no bundle made."; exit 1
fi
if [ "$(grep -c '^  llm-url: ' "$out/compose.yaml")" != 1 ]; then
    echo "The bundle's compose.yaml must have exactly one active llm-url: no bundle made."; exit 1
fi
LLM_API_KEY= docker compose -f "$out/compose.yaml" config -q \
    || { echo "The bundle's compose.yaml is not valid: no bundle made."; exit 1; }

cp la "$out/la"
chmod 755 "$out/la"
cp USAGE.txt "$out/USAGE.txt"
printf '# GPT-5.4 only: copy to .env next to compose.yaml, then chmod 600 .env\nLLM_API_KEY=\n' \
    > "$out/env.example"

cat > "$out/INSTALL.txt" <<EOF
la $version: install on an Ubuntu 22.04 or 24.04 server
The image is pre-built: nothing is built or downloaded here.

  sha256sum -c $name.tar.sha256
  tar xf $name.tar && sudo mv $name /opt/la && cd /opt/la
  sha256sum -c SHA256SUMS
  docker load < image.tar.gz
  sudo vi compose.yaml   # settings block: put the vLLM host in llm-url,
                         # or switch to the GPT-5.4 block
  # GPT-5.4 only:
  #   sudo cp env.example .env && sudo chmod 600 .env && sudo vi .env
  sudo ln -sf /opt/la/la /usr/local/bin/la
  la version
  la show ss
  la ask "which process listens on port 443"

How to use it: USAGE.txt (also: la help).

Needs: Docker with compose v2, and your user in the docker group
(or run la with sudo). Nothing else is installed on the server.

Every lookup runs in a new container that is removed afterwards.
Only "la ask" has a network, to reach the model. If the model can't
be reached, la ask says why and shows the KB search instead.

Update to a new bundle: install it the same way into /opt/la-new,
copy your settings block (and .env) across, then
  sudo rm -rf /opt/la && sudo mv /opt/la-new /opt/la
Remove: sudo rm /usr/local/bin/la && docker image rm $image
        && sudo rm -rf /opt/la
EOF

(cd "$out" && sha256sum image.tar.gz compose.yaml la USAGE.txt env.example INSTALL.txt > SHA256SUMS)
tar -C dist -cf "dist/$name.tar" "$name"
(cd dist && sha256sum "$name.tar" > "$name.tar.sha256")
rm -rf "$out"
echo "Bundle ready: dist/$name.tar ($(du -h "dist/$name.tar" | cut -f1)), checksum in dist/$name.tar.sha256"
echo "Carry both files to the server, then follow INSTALL.txt inside the tar."
