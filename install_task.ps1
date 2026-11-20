# Register a daily Windows scheduled task for Whimbox auto update.
# Run as Administrator:  powershell -ExecutionPolicy Bypass -File install_task.ps1
# Or simply double-click install_task.bat (it self-elevates).

$ErrorActionPreference = 'Stop'

# 自动推断奇想盒内置 Python：从脚本所在位置向上找 python-embedded\python.exe
$py     = $null
$dir    = $PSScriptRoot
for ($i = 0; $i -lt 5; $i++) {
    $candidate = Join-Path $dir 'python-embedded\python.exe'
    if (Test-Path $candidate) { $py = $candidate; break }
    $parent = Split-Path $dir -Parent
    if (-not $parent -or $parent -eq $dir) { break }
    $dir = $parent
}
$script = Join-Path $PSScriptRoot 'whimbox_updater.py'
$taskName = 'WhimboxAutoUpdate'

if (-not $py)     { throw "Embedded Python not found (向上未找到 python-embedded\python.exe). 请把本文件夹放在奇想盒安装目录内。" }
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
