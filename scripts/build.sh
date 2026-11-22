#!/bin/sh
# Builds the la image, runs the tests in it, then scans it with Trivy.
# Stops with an error if the KB check fails (inside the build), a test
# fails, or Trivy finds any HIGH or CRITICAL issue.
# Usage: scripts/build.sh [image-tag]   (default: the tag in compose.yaml)
set -eu
cd "$(dirname "$0")/.."
image="${1:-$(sed -n 's/^x-image: &image //p' compose.yaml)}"

echo "Building $image (the KB check runs inside the build) ..."
docker build -t "$image" .

echo "Running the tests in $image (no network, read-only) ..."
if docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL \
     -v "$PWD/tests:/tests:ro" --entrypoint python "$image" -m unittest discover -s /tests; then
    echo "Tests passed."
else
    echo "Tests FAILED (listed above). Do not ship $image."
    exit 1
fi

echo "Scanning $image with Trivy for HIGH and CRITICAL issues ..."
if trivy image -q --exit-code 1 --severity HIGH,CRITICAL "$image"; then
    echo "Scan clean: no HIGH or CRITICAL issues in $image."
else
    echo "Scan FAILED: $image has HIGH or CRITICAL issues (listed above). Do not ship it."
    exit 1
fi
size=$(docker image inspect -f '{{.Size}}' "$image" | awk '{printf "%.0f MB", $1/1e6}')
echo "Done: $image, $size."
