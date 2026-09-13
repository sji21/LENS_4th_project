param(
    [ValidateSet('status', 'apply', 'restore')]
    [string]$Action = 'apply',
    [string]$Source,
    [string]$Backup
)

$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '먼저 프로젝트 .venv에 Python 환경과 requirements를 설치하세요.'
}
$arguments = @('-X', 'utf8', '-m', 'scripts.manage_retrieval_data', $Action)
if ($Source) { $arguments += @('--source', [IO.Path]::GetFullPath($Source)) }
if ($Backup) { $arguments += @('--backup', [IO.Path]::GetFullPath($Backup)) }
Push-Location -LiteralPath $PSScriptRoot
try {
    & $pythonPath @arguments
    $resultCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $resultCode
