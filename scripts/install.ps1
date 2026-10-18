$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Install Python 3.11+ and add it to PATH.' }
python -m venv .venv
if ($LASTEXITCODE) { throw 'Failed to create virtual environment.' }
& .\.venv\Scripts\python -m pip install -e '.[dev]'
if ($LASTEXITCODE) { throw 'Dependency installation failed.' }
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
& .\.venv\Scripts\python -m autoyt doctor
Write-Host 'See SETUP.md to install FFmpeg/Ollama/Kokoro and authorize your channel. No OS scheduler has been registered.'
