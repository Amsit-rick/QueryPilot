[CmdletBinding()]
param(
    [string]$OutputDirectory = "D:\QueryPilot-deliveries"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path.TrimEnd("\", "/")
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$bundleName = "QueryPilot-development-$timestamp"
$resolvedOutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$stagingParent = Join-Path ([System.IO.Path]::GetTempPath()) "QueryPilot-development-delivery"
$stagingRoot = Join-Path $stagingParent ("$bundleName-" + [guid]::NewGuid().ToString("N"))
$bundleRoot = Join-Path $stagingRoot $bundleName

function Get-RelativePath([string]$path) {
    return $path.Substring($sourceRoot.Length).TrimStart("\", "/").Replace("\", "/")
}

function Test-Excluded([string]$relativePath) {
    $normalized = $relativePath.Replace("\", "/")
    $segments = $normalized -split "/"
    $name = $segments[-1]
    if ($segments | Where-Object { $_ -in @(
        ".git", ".venv", ".codex_tmp", ".workbuddy", "node_modules", "dist", "__pycache__", ".pytest_cache",
        "test-results", "playwright-report", "htmlcov", "exports", "backups", "artifacts"
    ) }) { return $true }
    if ($name -eq ".env" -or ($name -like ".env.*" -and $name -ne ".env.example")) { return $true }
    if ($name -like "*.pyc" -or $name -eq ".coverage") { return $true }
    if ($normalized -match "^data/audit.*\.jsonl$") { return $true }
    if ($normalized -match "^data/.*\.db-(shm|wal)$") { return $true }
    return $false
}

try {
    New-Item -ItemType Directory -Path $bundleRoot -Force | Out-Null
    Get-ChildItem -LiteralPath $sourceRoot -Recurse -File | ForEach-Object {
        if (($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return }
        $relative = Get-RelativePath $_.FullName
        if (Test-Excluded $relative) { return }
        $destination = Join-Path $bundleRoot ($relative.Replace("/", "\"))
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
    }

    $startHere = @(
        "Query Pilot 开发迁移包",
        "====================",
        "",
        "1. 完整解压到纯英文路径，例如 D:\QueryPilot\$bundleName",
        "2. 安装 Python 3.12、Node.js 22、Docker Desktop 并启动 Docker Desktop",
        "3. 在新的 CMD 中进入包含 app、frontend、scripts 的项目根目录",
        "4. 首次安装：scripts\setup_local.cmd",
        "5. 使用 admin / 20040508 登录；默认数据源为 MySQL，SQLite 演示库仍可在下拉框选择",
        "6. 后续启动：scripts\start_local.cmd",
        "7. 继续开发前端：cd frontend && npm.cmd run dev",
        "8. 运行后端测试：.venv\Scripts\python.exe -m pytest -q",
        "9. 前端构建：cd frontend && npm.cmd run build",
        "",
        "本包包含演示 SQLite 数据库、全部源码、迁移、脚本、测试和项目文档。",
        "本包不包含 .env、Git 元数据、虚拟环境、依赖目录、审计日志、导出文件和测试缓存。",
        "如果 3307 被其他 MySQL 占用，请先停止其他服务；启动脚本不会误连未知数据库。"
    ) -join [Environment]::NewLine
    Set-Content -LiteralPath (Join-Path $bundleRoot "START_HERE.txt") -Value $startHere -Encoding UTF8

    $branch = (& git -C $sourceRoot branch --show-current).Trim()
    $headCommit = (& git -C $sourceRoot rev-parse --short HEAD).Trim()
    $payloadFiles = Get-ChildItem -LiteralPath $bundleRoot -Recurse -File | Sort-Object FullName | ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($bundleRoot.Length).TrimStart("\", "/").Replace("\", "/")
            size_bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    [ordered]@{
        format_version = 1
        generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        product = "Query Pilot"
        bundle_type = "development-migration"
        bundle_name = $bundleName
        source_branch = $branch
        source_head = $headCommit
        includes_worktree_changes = $true
        secrets_included = $false
        payload_file_count = @($payloadFiles).Count
        files = @($payloadFiles)
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $bundleRoot "DELIVERY-MANIFEST.json") -Encoding UTF8

    New-Item -ItemType Directory -Path $resolvedOutputDirectory -Force | Out-Null
    $zipPath = Join-Path $resolvedOutputDirectory "$bundleName.zip"
    Compress-Archive -LiteralPath $bundleRoot -DestinationPath $zipPath -CompressionLevel Optimal
    $zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$zipPath.sha256" -Value "$zipHash  $bundleName.zip" -Encoding ASCII
    [ordered]@{ zip_path = $zipPath; sha256_path = "$zipPath.sha256"; sha256 = $zipHash; size_bytes = (Get-Item $zipPath).Length; payload_file_count = @($payloadFiles).Count } | ConvertTo-Json
}
finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        $expected = [System.IO.Path]::GetFullPath($stagingParent).TrimEnd("\") + "\"
        $actual = [System.IO.Path]::GetFullPath($stagingRoot)
        if (-not $actual.StartsWith($expected, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Refusing to remove staging directory outside expected root." }
        Remove-Item -LiteralPath $actual -Recurse -Force
    }
}
