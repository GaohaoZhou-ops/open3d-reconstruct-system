[CmdletBinding()]
param(
    [switch]$Offline
)

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ProjectRoot = Split-Path -Parent $PSCommandPath
$UvVersion = "0.11.16"
$UvArchiveSha256 = "dd9d6d6554bfab265bfa98aa8e8a406c5c3a7b97582f93de1f4d48d9154a0395"
$K4aVersion = "1.4.1"
$K4aPackageSha256 = "6c512a20c4a82b80e02b0f6d4a6cda51e88d4893cd47ab85c7bca37cd364c976"

$DownloadDir = Join-Path $ProjectRoot ".cache\downloads"
$ToolsDir = Join-Path $ProjectRoot ".tools-windows"
$UvExe = Join-Path $ToolsDir "uv.exe"
$PythonDir = Join-Path $ProjectRoot ".python-windows"
$VenvDir = Join-Path $ProjectRoot ".venv-windows"
$K4aRoot = Join-Path $ProjectRoot ".deps\k4a-windows"
$K4aLibDir = Join-Path $K4aRoot "lib\native\amd64\release"
$K4aMarker = Join-Path $K4aRoot ".installed-$K4aVersion"

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "setup.ps1 只能在 Windows PowerShell 中运行。WSL2 请使用 ./setup.sh。"
}
if ($ProjectRoot.StartsWith("\\")) {
    throw (
        "Windows 原生环境不能安装在 WSL 的 UNC 路径中（$ProjectRoot）。" +
        "uv 和 Windows 文件锁在该文件系统上不可靠；请把 Windows 工作副本放在 NTFS " +
        "路径（例如 C:\src\open3d-reconstruct-system），再运行 .\setup.ps1。"
    )
}
$Architecture = [Environment]::GetEnvironmentVariable("PROCESSOR_ARCHITEW6432")
if ([string]::IsNullOrWhiteSpace($Architecture)) {
    $Architecture = [Environment]::GetEnvironmentVariable("PROCESSOR_ARCHITECTURE")
}
if (-not [Environment]::Is64BitOperatingSystem -or $Architecture -ne "AMD64") {
    throw "不支持的 Windows 架构: $Architecture（当前仅支持 Windows x64）。"
}

function New-LocalDirectory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Get-VerifiedDownload(
    [string]$Url,
    [string]$Destination,
    [string]$ExpectedSha256
) {
    $NeedsDownload = -not (Test-Path -LiteralPath $Destination -PathType Leaf)
    if (-not $NeedsDownload) {
        $CachedHash = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($CachedHash -ne $ExpectedSha256) {
            if ($Offline) {
                throw "离线缓存校验失败: $Destination`n期望 $ExpectedSha256，实际 $CachedHash"
            }
            Write-Host "缓存校验失败，重新下载 $(Split-Path -Leaf $Destination)"
            Remove-Item -LiteralPath $Destination -Force
            $NeedsDownload = $true
        }
    }
    if ($NeedsDownload) {
        if ($Offline) {
            throw "离线模式缺少缓存: $Destination"
        }
        $Temporary = "$Destination.part"
        Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
        Write-Host "下载 $(Split-Path -Leaf $Destination)"
        Add-Type -AssemblyName System.Net.Http
        $Client = [Net.Http.HttpClient]::new()
        $Client.Timeout = [TimeSpan]::FromMinutes(30)
        $Client.DefaultRequestHeaders.UserAgent.ParseAdd("open3d-reconstruct-setup/$UvVersion")
        try {
            $Bytes = $Client.GetByteArrayAsync($Url).GetAwaiter().GetResult()
            [IO.File]::WriteAllBytes($Temporary, $Bytes)
            # WSL's UNC file server may leave zero-filled allocation padding at
            # EOF after WriteAllBytes; an explicit SetLength keeps hashes exact.
            $Stream = [IO.File]::Open(
                $Temporary,
                [IO.FileMode]::Open,
                [IO.FileAccess]::Write,
                [IO.FileShare]::Read
            )
            try {
                $Stream.SetLength($Bytes.LongLength)
                $Stream.Flush($true)
            }
            finally {
                $Stream.Dispose()
            }
        }
        finally {
            $Client.Dispose()
        }
        Move-Item -LiteralPath $Temporary -Destination $Destination -Force
    }
    $Actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $ExpectedSha256) {
        throw "校验失败: $Destination`n期望 $ExpectedSha256，实际 $Actual"
    }
}

foreach ($Directory in @(
    $DownloadDir,
    $ToolsDir,
    (Join-Path $ProjectRoot ".deps"),
    $PythonDir,
    (Join-Path $ProjectRoot ".cache\uv-windows"),
    (Join-Path $ProjectRoot ".cache\windows\matplotlib"),
    (Join-Path $ProjectRoot ".cache\open3d-data"),
    (Join-Path $ProjectRoot "data\recordings"),
    (Join-Path $ProjectRoot "data\datasets")
)) {
    New-LocalDirectory $Directory
}

if (-not (Test-Path -LiteralPath $UvExe -PathType Leaf)) {
    $UvArchive = Join-Path $DownloadDir "uv-$UvVersion-x86_64-pc-windows-msvc.zip"
    Get-VerifiedDownload `
        "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" `
        $UvArchive `
        $UvArchiveSha256
    $UvStaging = Join-Path $ToolsDir ".uv-extract-$PID"
    Remove-Item -LiteralPath $UvStaging -Recurse -Force -ErrorAction SilentlyContinue
    try {
        Expand-Archive -LiteralPath $UvArchive -DestinationPath $UvStaging -Force
        foreach ($Name in @("uv.exe", "uvw.exe", "uvx.exe")) {
            $Source = Join-Path $UvStaging $Name
            if (Test-Path -LiteralPath $Source -PathType Leaf) {
                Move-Item -LiteralPath $Source -Destination (Join-Path $ToolsDir $Name) -Force
            }
        }
    }
    finally {
        Remove-Item -LiteralPath $UvStaging -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$K4aFiles = @("k4a.dll", "k4arecord.dll", "depthengine_2_0.dll")
$K4aReady = Test-Path -LiteralPath $K4aMarker -PathType Leaf
foreach ($Name in $K4aFiles) {
    $K4aReady = $K4aReady -and (Test-Path -LiteralPath (Join-Path $K4aLibDir $Name) -PathType Leaf)
}
if (-not $K4aReady) {
    $K4aPackage = Join-Path $DownloadDir "Microsoft.Azure.Kinect.Sensor.$K4aVersion.nupkg"
    Get-VerifiedDownload `
        "https://www.nuget.org/api/v2/package/Microsoft.Azure.Kinect.Sensor/$K4aVersion" `
        $K4aPackage `
        $K4aPackageSha256
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $K4aStaging = Join-Path (Join-Path $ProjectRoot ".deps") ".k4a-windows-$PID"
    Remove-Item -LiteralPath $K4aStaging -Recurse -Force -ErrorAction SilentlyContinue
    try {
        [IO.Compression.ZipFile]::ExtractToDirectory($K4aPackage, $K4aStaging)
        $StagedLibDir = Join-Path $K4aStaging "lib\native\amd64\release"
        foreach ($Name in $K4aFiles) {
            if (-not (Test-Path -LiteralPath (Join-Path $StagedLibDir $Name) -PathType Leaf)) {
                throw "Azure Kinect NuGet 包缺少运行库: $Name"
            }
        }
        Remove-Item -LiteralPath $K4aRoot -Recurse -Force -ErrorAction SilentlyContinue
        Move-Item -LiteralPath $K4aStaging -Destination $K4aRoot
        Set-Content -LiteralPath $K4aMarker -Value $K4aVersion -Encoding Ascii
    }
    finally {
        Remove-Item -LiteralPath $K4aStaging -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Azure Kinect SDK 许可条款: $(Join-Path $K4aRoot 'LICENSE.txt')"
Write-Host "使用 Azure Kinect SDK 即表示接受该软件随附的许可条款。"

$env:UV_CACHE_DIR = Join-Path $ProjectRoot ".cache\uv-windows"
$env:UV_PYTHON_INSTALL_DIR = $PythonDir
$env:UV_PYTHON_BIN_DIR = Join-Path $PythonDir "bin"
$env:UV_TOOL_DIR = Join-Path $ToolsDir "uv-tools"
$env:UV_TOOL_BIN_DIR = Join-Path $ToolsDir "uv-tool-bin"
$env:UV_PROJECT_ENVIRONMENT = $VenvDir
$env:UV_PYTHON_PREFERENCE = "only-managed"
$env:UV_PYTHON_INSTALL_BIN = "0"
$env:PYTHONNOUSERSITE = "1"

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (Test-Path -LiteralPath $VenvDir -PathType Container) {
    $ValidVenv = Test-Path -LiteralPath $VenvPython -PathType Leaf
    if ($ValidVenv) {
        $BasePrefix = & $VenvPython -I -c "import sys; print(sys.base_prefix)" 2>$null
        $ValidVenv = $LASTEXITCODE -eq 0 -and $BasePrefix -and (
            [IO.Path]::GetFullPath($BasePrefix).StartsWith(
                [IO.Path]::GetFullPath($PythonDir),
                [StringComparison]::OrdinalIgnoreCase
            )
        )
    }
    if (-not $ValidVenv) {
        Write-Host "检测到 .venv-windows 未使用项目内 Python，正在安全重建虚拟环境……"
        Remove-Item -LiteralPath $VenvDir -Recurse -Force
    }
}

$LockFile = Join-Path $ProjectRoot "uv.lock"
if (-not (Test-Path -LiteralPath $LockFile -PathType Leaf)) {
    if ($Offline) {
        throw "离线模式下缺少 uv.lock"
    }
    & $UvExe lock --project $ProjectRoot
    if ($LASTEXITCODE -ne 0) {
        throw "uv lock 失败（退出码 $LASTEXITCODE）"
    }
}

$SyncArguments = @("sync", "--project", $ProjectRoot, "--frozen")
if ($Offline) {
    $SyncArguments += "--offline"
}
& $UvExe @SyncArguments
if ($LASTEXITCODE -ne 0) {
    throw "uv sync 失败（退出码 $LASTEXITCODE）"
}

Write-Host ""
Write-Host "安装完成（Windows x64）：Windows Python、虚拟环境和依赖均位于项目目录。"
Write-Host "  Python: $PythonDir"
Write-Host "  venv:  $VenvDir"
Write-Host "Azure Kinect K4A 1.4.1 DLL 已安装在项目内；RealSense 已由 Open3D wheel 静态集成。"
Write-Host ""
& (Join-Path $ProjectRoot "open3d-reconstruct.ps1") doctor
exit $LASTEXITCODE
