#!/usr/bin/env bash
set -euo pipefail

CLOUD_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$CLOUD_PROJECT_DIR/open3d-reconstruct" wizard "$@"
