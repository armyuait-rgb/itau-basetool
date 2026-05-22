#!/usr/bin/env bash
# Build the workability image and run config + dependency smoke checks.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${MEGATOOL_SMOKE_IMAGE:-megatool-proxy-workability:local}"

cd "$ROOT"
echo "==> Building $IMAGE"
docker build -t "$IMAGE" .

echo "==> Dry-run (config parse only)"
docker run --rm "$IMAGE" --dry-run

echo "==> Smoke (megatool imports + config.json)"
docker run --rm "$IMAGE" --smoke

echo "==> All container smoke checks passed"
