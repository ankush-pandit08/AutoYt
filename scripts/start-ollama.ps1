# Optional launcher for the workspace-local portable CPU runtime.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$binary = Join-Path $root 'data/tools/ollama/ollama.exe'
if (-not (Test-Path -LiteralPath $binary)) { throw 'Install Ollama normally, or place its portable runtime under data/tools/ollama.' }
$env:OLLAMA_MODELS = Join-Path $root 'data/models'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_NO_CLOUD = '1'
& $binary serve
exit $LASTEXITCODE
