# Registers a Windows scheduled task "BukuKira" that starts the bot at log on,
# hidden (no window), and keeps it running. Run once from the bot folder:
#   powershell -ExecutionPolicy Bypass -File install_autostart.ps1
# If you get "Access is denied", open PowerShell with "Run as administrator" and try again.

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $dir ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { Write-Error "Not found: $pythonw (create the .venv first, see SETUP.md section 4)"; exit 1 }

$action   = New-ScheduledTaskAction -Execute $pythonw -Argument "run_forever.py" -WorkingDirectory $dir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
              -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
              -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName "BukuKira" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "BukuKira"
Write-Host "Done. BukuKira is running now and will start by itself every time you log on."
Write-Host "Check it: send /start to the bot in Telegram. Log file: $dir\bot.log"
