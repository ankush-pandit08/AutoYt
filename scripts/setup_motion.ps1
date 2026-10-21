param([string]$Node = 'node')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$project = Join-Path $root 'src/autoyt/remotion'
$npm = Get-Command npm.cmd -ErrorAction Stop
Push-Location $project
try {
    & $npm.Source ci --cache (Join-Path $root 'data/cache/npm-motion') --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Remotion dependencies could not be installed.' }
    & $Node render.mjs --setup
    if ($LASTEXITCODE -ne 0) { throw 'Remotion browser could not be installed.' }
} finally { Pop-Location }
Write-Output 'Local motion rendering is ready. Select a mode in the dashboard.'
