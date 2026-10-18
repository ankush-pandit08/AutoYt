# Current-user, limited-privilege task; starts after Windows sign-in, without a console window.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$launcher = Join-Path $PSScriptRoot 'start-autoyt.ps1'
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`" -NoBrowser -Supervise" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'AutoYT Personal Dashboard' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Local AutoYT dashboard and daily personal-channel scheduler. No credentials in task arguments.' -Force | Select-Object TaskName, State
