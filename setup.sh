#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
UV_VERSION="0.11.16"
UV_INSTALLER_SHA256="b9f925505899533f36a3acfdf8684c661ff2d5c8735f759fca768367b5996123"
K4A_VERSION="1.4.1"
K4A_DEB_SHA256="c1c63f81641eed1326136a44e5a5fd229e1e6315b2b20b2dddb5523a556c9329"

DOWNLOAD_DIR="$PROJECT_ROOT/.cache/downloads"
TOOLS_DIR="$PROJECT_ROOT/.tools"
UV_BIN="$TOOLS_DIR/uv"
K4A_ROOT="$PROJECT_ROOT/.deps/k4a"
K4A_MARKER="$K4A_ROOT/.installed-$K4A_VERSION"
K4A_LIB_DIR="$K4A_ROOT/usr/lib/x86_64-linux-gnu"

usage() {
    echo "用法: ./setup.sh [--offline]"
    echo "  默认下载并安装全部依赖到当前项目；--offline 只使用已有缓存。"
}

OFFLINE=0
case "${1:-}" in
    "") ;;
    --offline) OFFLINE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
    echo "当前本地隔离方案仅支持 Linux x86_64。" >&2
    exit 1
fi

for command_name in curl sha256sum dpkg-deb; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "缺少系统基础命令: $command_name" >&2
        exit 1
    fi
done

mkdir -p "$DOWNLOAD_DIR" "$TOOLS_DIR" "$PROJECT_ROOT/.deps" \
    "$PROJECT_ROOT/.python" "$PROJECT_ROOT/.cache/uv" \
    "$PROJECT_ROOT/.cache/matplotlib" "$PROJECT_ROOT/.cache/open3d-data" \
    "$PROJECT_ROOT/data/recordings" \
    "$PROJECT_ROOT/data/datasets"

download() {
    local url="$1"
    local destination="$2"
    if [[ -s "$destination" ]]; then
        return
    fi
    if [[ "$OFFLINE" == "1" ]]; then
        echo "离线模式缺少缓存: $destination" >&2
        exit 1
    fi
    local temporary="$destination.part"
    rm -f -- "$temporary"
    echo "下载 $(basename -- "$destination")"
    if ! curl --proto '=https' --tlsv1.2 -fL --retry 3 \
        --connect-timeout 15 --max-time 1800 "$url" -o "$temporary"; then
        echo "代理连接失败，尝试 IPv4 直连……" >&2
        curl -4 --noproxy '*' --proto '=https' --tlsv1.2 -fL --retry 3 \
            --connect-timeout 15 --max-time 1800 "$url" -o "$temporary"
    fi
    mv -- "$temporary" "$destination"
}

verify_sha256() {
    local expected="$1"
    local file="$2"
    local actual
    actual="$(sha256sum "$file" | cut -d ' ' -f 1)"
    if [[ "$actual" != "$expected" ]]; then
        echo "校验失败: $file" >&2
        echo "期望 $expected，实际 $actual" >&2
        exit 1
    fi
}

run_online_uv() {
    if "$@"; then
        return
    fi
    echo "代理环境下安装失败，尝试不使用代理重新连接……" >&2
    env -u HTTPS_PROXY -u HTTP_PROXY -u https_proxy -u http_proxy \
        -u ALL_PROXY -u all_proxy "$@"
}

if [[ ! -x "$UV_BIN" ]]; then
    UV_INSTALLER="$DOWNLOAD_DIR/uv-installer-$UV_VERSION.sh"
    download "https://releases.astral.sh/github/uv/releases/download/$UV_VERSION/uv-installer.sh" "$UV_INSTALLER"
    verify_sha256 "$UV_INSTALLER_SHA256" "$UV_INSTALLER"
    UV_UNMANAGED_INSTALL="$TOOLS_DIR" UV_NO_MODIFY_PATH=1 sh "$UV_INSTALLER"
fi

if [[ ! -f "$K4A_MARKER" || ! -f "$K4A_LIB_DIR/libk4a.so.1.4" \
    || ! -f "$K4A_LIB_DIR/libk4arecord.so.1.4" \
    || ! -f "$K4A_LIB_DIR/libk4a1.4/libdepthengine.so.2.0" ]]; then
    K4A_DEB="$DOWNLOAD_DIR/libk4a1.4_${K4A_VERSION}_amd64.deb"
    download "https://packages.microsoft.com/ubuntu/18.04/prod/pool/main/libk/libk4a1.4/libk4a1.4_${K4A_VERSION}_amd64.deb" "$K4A_DEB"
    verify_sha256 "$K4A_DEB_SHA256" "$K4A_DEB"
    rm -rf -- "$K4A_ROOT"
    mkdir -p "$K4A_ROOT"
    dpkg-deb -x "$K4A_DEB" "$K4A_ROOT"
    touch "$K4A_MARKER"
fi

echo "Azure Kinect SDK 许可条款: $K4A_ROOT/usr/share/doc/libk4a1.4/LICENSE.txt"
echo "使用 Azure Kinect SDK 即表示接受该软件随附的许可条款。"

export UV_CACHE_DIR="$PROJECT_ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PROJECT_ROOT/.python"
export UV_PYTHON_BIN_DIR="$PROJECT_ROOT/.python/bin"
export UV_TOOL_DIR="$PROJECT_ROOT/.tools/uv-tools"
export UV_TOOL_BIN_DIR="$PROJECT_ROOT/.tools/uv-tool-bin"
export UV_PYTHON_PREFERENCE=only-managed
export UV_PYTHON_INSTALL_BIN=0
export PYTHONNOUSERSITE=1

if [[ ! -f "$PROJECT_ROOT/uv.lock" ]]; then
    if [[ "$OFFLINE" == "1" ]]; then
        echo "离线模式下缺少 uv.lock" >&2
        exit 1
    fi
    run_online_uv "$UV_BIN" lock
fi

if [[ "$OFFLINE" == "1" ]]; then
    "$UV_BIN" sync --frozen --offline
else
    run_online_uv "$UV_BIN" sync --frozen
fi

chmod +x "$PROJECT_ROOT/open3d-reconstruct" "$PROJECT_ROOT/setup.sh"

echo
echo "安装完成：Python、虚拟环境、Python 包和 Azure Kinect SDK 均位于："
echo "  $PROJECT_ROOT"
echo "RealSense librealsense 已由项目 .venv 中的 Open3D wheel 静态集成。"
echo
"$PROJECT_ROOT/open3d-reconstruct" doctor
