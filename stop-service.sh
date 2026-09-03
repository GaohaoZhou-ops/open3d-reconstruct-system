#!/usr/bin/env bash
set -euo pipefail

SERVICE_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$SERVICE_PROJECT_DIR/open3d-reconstruct" service stop "$@"
