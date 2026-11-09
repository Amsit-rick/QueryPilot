[CmdletBinding()]
param(
    [string]$OutputDirectory = "D:\QueryPilot-deliveries"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path.TrimEnd("\", "/")
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$bundleName = "QueryPilot-test-$timestamp"
$resolvedOutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$stagingParent = Join-Path ([System.IO.Path]::GetTempPath()) "QueryPilot-delivery"
$stagingRoot = Join-Path $stagingParent ("$bundleName-" + [guid]::NewGuid().ToString("N"))
$bundleRoot = Join-Path $stagingRoot $bundleName

$directoryAllowlist = @(
    ".github",
    "app",
    "deploy",
    "docs",
    "frontend",
    "migrations",
    "scripts",
    "tests"
)

$dataAllowlist = @(
    "data/access_policy.json",
    "data/capability_eval.json",
    "data/catalog.json",
    "data/datasources.json",
    "data/eval.json",
    "data/relationship_eval.json",
    "data/sql_security_eval.json"
)

$rootFileAllowlist = @(
    ".dockerignore",
    ".env.example",
    ".gitignore",
    "Dockerfile",
    "docker-compose.app.yml",
    "docker-compose.mysql.yml",
    "docker-compose.postgres.yml",
    "README.md",
    "requirements.txt",
    "requirements-connectors.txt",
    "HANDOFF.md",
    "项目改造计划书.md",
    "项目开发日志.md",
    "项目计划清单.md",
    "部署运行手册.md",
    "功能介绍及使用详情书.md",
    "项目介绍详情.md"
)

function Get-NormalizedRelativePath {
    param([Parameter(Mandatory = $true)][string]$FullName)

    return $FullName.Substring($sourceRoot.Length).TrimStart("\", "/").Replace("\", "/")
}

function Test-ExcludedPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $normalized = $RelativePath.Replace("\", "/")
    $segments = $normalized -split "/"
    $name = $segments[-1]

    if ($segments | Where-Object { $_ -in @(
        ".git", ".venv", "node_modules", "dist", "test-results",
        "playwright-report", "artifacts", "__pycache__", ".pytest_cache",
        "htmlcov", "exports", "backups"
    ) }) {
        return $true
    }

    if ($name -eq ".env" -or ($name -like ".env.*" -and $name -ne ".env.example")) {
        return $true
    }

    if ($name -like "*.pyc" -or $name -eq ".coverage") {
        return $true
    }

    if ($normalized -match "^data/(audit.*\.jsonl|.*\.(db|sqlite|sqlite3)(-.+)?$)") {
        return $true
    }

    return $false
}

function Copy-DeliveryFile {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $normalized = $RelativePath.Replace("\", "/")
    if (Test-ExcludedPath $normalized) {
        throw "Allowlisted file was rejected by an exclusion rule: $normalized"
    }

    $source = Join-Path $sourceRoot ($normalized.Replace("/", "\"))
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required delivery file is missing: $normalized"
    }

    $destination = Join-Path $bundleRoot ($normalized.Replace("/", "\"))
    $destinationDirectory = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Force
}

try {
    New-Item -ItemType Directory -Path $bundleRoot -Force | Out-Null

    foreach ($directory in $directoryAllowlist) {
        $directoryPath = Join-Path $sourceRoot $directory
        if (-not (Test-Path -LiteralPath $directoryPath -PathType Container)) {
            throw "Required delivery directory is missing: $directory"
        }

        Get-ChildItem -LiteralPath $directoryPath -Recurse -File | ForEach-Object {
            if (($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                return
            }
            $relativePath = Get-NormalizedRelativePath $_.FullName
            if (-not (Test-ExcludedPath $relativePath)) {
                Copy-DeliveryFile $relativePath
            }
        }
    }

    foreach ($relativePath in $dataAllowlist + $rootFileAllowlist) {
        Copy-DeliveryFile $relativePath
    }

    $startHere = @(
        "Query Pilot 测试交付包",
        "=====================",
        "",
        "1. 将整个文件夹解压到纯英文路径，例如 D:\QueryPilot\$bundleName",
        "2. 安装 Python 3.12、Node.js 22 和 Docker Desktop，并启动 Docker Desktop",
        "3. 在新的 CMD 窗口进入解压目录",
        "4. 首次运行：scripts\setup_local.cmd",
        "5. 使用 admin / 20040508 登录，验证完整管理员工作区",
        "6. 检查运行状态：scripts\check_stack.cmd",
        "7. 打开前端：http://127.0.0.1:5173",
        "8. API 文档：http://127.0.0.1:8000/docs",
        "",
        "详细步骤、测试场景及故障排查见 docs\delivery-test-guide.md。",
        "本包不含 .env、真实凭据、本机数据库、审计日志、导出结果或依赖目录。"
    ) -join [Environment]::NewLine
    Set-Content -LiteralPath (Join-Path $bundleRoot "START_HERE.txt") -Value $startHere -Encoding UTF8

    $branch = (& git -C $sourceRoot branch --show-current).Trim()
    $headCommit = (& git -C $sourceRoot rev-parse --short HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $headCommit) {
        throw "Unable to read Git revision metadata."
    }

    $payloadFiles = Get-ChildItem -LiteralPath $bundleRoot -Recurse -File |
        Sort-Object FullName |
        ForEach-Object {
            [ordered]@{
                path = $_.FullName.Substring($bundleRoot.Length).TrimStart("\", "/").Replace("\", "/")
                size_bytes = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }

    $manifest = [ordered]@{
        format_version = 1
        generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        product = "Query Pilot"
        bundle_name = $bundleName
        source_branch = $branch
        source_head = $headCommit
        includes_worktree_changes = $true
        payload_file_count = @($payloadFiles).Count
        secrets_included = $false
        excluded_runtime_data = $true
        validation_scope = "Local automated checks passed; Oracle, SQL Server, and ClickHouse still require real-instance acceptance."
        files = @($payloadFiles)
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $bundleRoot "DELIVERY-MANIFEST.json") -Encoding UTF8

    New-Item -ItemType Directory -Path $resolvedOutputDirectory -Force | Out-Null
    $zipPath = Join-Path $resolvedOutputDirectory "$bundleName.zip"
    Compress-Archive -LiteralPath $bundleRoot -DestinationPath $zipPath -CompressionLevel Optimal

    $zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $hashPath = "$zipPath.sha256"
    Set-Content -LiteralPath $hashPath -Value "$zipHash  $bundleName.zip" -Encoding ASCII

    [ordered]@{
        zip_path = $zipPath
        sha256_path = $hashPath
        sha256 = $zipHash
        size_bytes = (Get-Item -LiteralPath $zipPath).Length
        payload_file_count = @($payloadFiles).Count
        source_branch = $branch
        source_head = $headCommit
    } | ConvertTo-Json
}
finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        $resolvedStagingParent = [System.IO.Path]::GetFullPath($stagingParent).TrimEnd("\") + "\"
        $resolvedStagingRoot = [System.IO.Path]::GetFullPath($stagingRoot)
        if (-not $resolvedStagingRoot.StartsWith($resolvedStagingParent, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a staging directory outside the expected temporary root."
        }
        Remove-Item -LiteralPath $resolvedStagingRoot -Recurse -Force
    }
}
