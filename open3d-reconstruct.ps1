[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$CommandArguments
)

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$ProjectRoot = Split-Path -Parent $PSCommandPath
$Python = Join-Path $ProjectRoot ".venv-windows\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Write-Error "Windows 环境尚未安装。请先运行：.\setup.ps1"
    exit 2
}

$env:OPEN3D_RECONSTRUCT_ROOT = $ProjectRoot
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUTF8 = "1"
$env:XDG_CACHE_HOME = Join-Path $ProjectRoot ".cache\windows"
$env:OPEN3D_DATA_ROOT = Join-Path $ProjectRoot ".cache\open3d-data"
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".cache\windows\matplotlib"
$env:UV_CACHE_DIR = Join-Path $ProjectRoot ".cache\uv-windows"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $ProjectRoot ".python-windows"
$env:UV_PROJECT_ENVIRONMENT = Join-Path $ProjectRoot ".venv-windows"
$K4aLibDir = Join-Path $ProjectRoot ".deps\k4a-windows\lib\native\amd64\release"
$env:K4A_LIB_DIR = $K4aLibDir
$env:PATH = $K4aLibDir + ";" + $env:PATH
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue

& $Python -B -I -u -X utf8 -m open3d_reconstruct @CommandArguments
exit $LASTEXITCODE
