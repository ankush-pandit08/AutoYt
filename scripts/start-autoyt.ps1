param([switch]$NoBrowser, [switch]$Supervise)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts/install.ps1 first.' }
$ready = $false
try { $null = Invoke-RestMethod 'http://127.0.0.1:8765/api/status' -TimeoutSec 3; $ready = $true } catch {}
if (-not $ready) {
    $logs = Join-Path $root 'data/logs'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $provider = & $python -c "from autoyt.config import Settings; print(Settings().llm_provider)"
    if ($provider -eq 'ollama') {
        try { $null = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3 }
        catch {
            $portable = Join-Path $root 'data/tools/ollama/ollama.exe'
            if (Test-Path -LiteralPath $portable) {
                $launcher = Join-Path $PSScriptRoot 'start-ollama.ps1'
                Start-Process powershell.exe -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$launcher`"") -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'ollama-startup.log') -RedirectStandardError (Join-Path $logs 'ollama-startup-error.log')
            } else {
                $ollama = Get-Command ollama -ErrorAction SilentlyContinue
                if ($ollama) {
                    $env:OLLAMA_HOST = '127.0.0.1:11434'
                    $env:OLLAMA_NO_CLOUD = '1'
                    Start-Process $ollama.Source -ArgumentList 'serve' -WorkingDirectory $root -WindowStyle Hidden
                }
            }
        }
    }
    $dashboardProcess = Start-Process $python -ArgumentList @('-m','autoyt','dashboard') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'dashboard-startup.log') -RedirectStandardError (Join-Path $logs 'dashboard-startup-error.log') -PassThru
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        Start-Sleep -Seconds 1
        try { $null = Invoke-RestMethod 'http://127.0.0.1:8765/api/status' -TimeoutSec 2; $ready = $true; break } catch {}
    }
    if (-not $ready) { throw 'Dashboard did not start. Check data/logs/dashboard-startup-error.log; stop a separate scheduler if one is running.' }
}
Write-Output 'AutoYT is running: http://127.0.0.1:8765'
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8765' }
if ($Supervise -and $dashboardProcess) {
    $dashboardProcess.WaitForExit()
    if ($dashboardProcess.ExitCode -ne 0) { exit 1 }
}
