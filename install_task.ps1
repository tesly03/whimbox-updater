# Register a daily Windows scheduled task for Whimbox auto update.
# Run as Administrator:  powershell -ExecutionPolicy Bypass -File install_task.ps1
# Or simply double-click install_task.bat (it self-elevates).

$ErrorActionPreference = 'Stop'

$py     = 'C:\Program Files\whimbox_app\python-embedded\python.exe'
$script = Join-Path $PSScriptRoot 'whimbox_updater.py'
$taskName = 'WhimboxAutoUpdate'

if (-not (Test-Path $py))     { throw "Embedded Python not found: $py" }
if (-not (Test-Path $script)) { throw "Updater script not found: $script" }

# Change 09:00 to whatever time you prefer.
$trigger  = New-ScheduledTaskTrigger -Daily -At '09:00'
$action   = New-ScheduledTaskAction -Execute $py -Argument ('"{0}" --quiet --no-elevate' -f $script)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "OK: task '$taskName' registered (daily 09:00, highest privileges)."
Write-Host "To remove:  Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
Write-Host "To run now: Start-ScheduledTask -TaskName '$taskName'"

# Uncomment the next line if you also want a run at every logon:
# Register-ScheduledTask -TaskName 'WhimboxAutoUpdateLogon' -Action $action -Trigger (New-ScheduledTaskTrigger -AtLogOn) -Principal $principal -Settings $settings -Force | Out-Null
