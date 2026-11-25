# Register a daily task. This file is ASCII for Windows PowerShell 5.1.
# Run install_task.bat as Administrator, or pass explicit paths here.
param(
    [string]$AppDir = '',
    [string]$DataDir = '',
    [ValidatePattern('\A([01][0-9]|2[0-3]):[0-5][0-9]\z')]
    [string]$Time = '09:00',
    [string]$TaskUser = '',
    [string]$UserLocalAppData = '',
    [switch]$PauseOnExit
)
$ErrorActionPreference = 'Stop'
$exitCode = 0

try {
    if (-not $AppDir) {
        $directory = $PSScriptRoot
        for ($index = 0; $index -lt 12; $index++) {
            if ((Test-Path (Join-Path $directory 'python-embedded\python.exe')) -and
                (Test-Path (Join-Path $directory 'resources\app.asar'))) {
                $AppDir = $directory
                break
            }
            $parent = Split-Path $directory -Parent
            if (-not $parent -or $parent -eq $directory) { break }
            $directory = $parent
        }
    }
    if (-not $AppDir) { throw 'Whimbox was not found. Extract the updater inside its installation directory, or pass -AppDir.' }
    $AppDir = (Resolve-Path -LiteralPath $AppDir).Path
    $embedded = Join-Path $AppDir 'python-embedded\python.exe'
    if (-not (Test-Path -LiteralPath $embedded)) { throw "Embedded Python was not found: $embedded" }

    $executable = Join-Path $PSScriptRoot 'whimbox-updater.exe'
    $prefix = @()
    if (-not (Test-Path -LiteralPath $executable)) {
        $executable = $embedded
        $source = Join-Path $PSScriptRoot 'whimbox_updater.py'
        if (-not (Test-Path -LiteralPath $source)) { throw "Updater source was not found: $source" }
        $prefix = @($source)
    }

    if (-not $DataDir) {
        $previousLocalAppData = $env:LOCALAPPDATA
        try {
            if ($UserLocalAppData) { $env:LOCALAPPDATA = $UserLocalAppData }
            $output = & $executable @prefix --app-dir $AppDir --print-data-dir
            if ($LASTEXITCODE -ne 0) { throw 'Reading the updater data directory failed.' }
            $DataDir = ($output | Select-Object -Last 1).Trim()
        } finally {
            $env:LOCALAPPDATA = $previousLocalAppData
        }
    }
    $DataDir = [System.IO.Path]::GetFullPath($DataDir)
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null

    $arguments = @($prefix) + @('--app-dir', $AppDir, '--data-dir', $DataDir, '--quiet', '--no-elevate')
    $argumentLine = ($arguments | ForEach-Object {
        $escaped = $_ -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1'
        '"{0}"' -f $escaped
    }) -join ' '

    $resolvedUser = if ($TaskUser) { $TaskUser } else { "$env:USERDOMAIN\$env:USERNAME" }
    $taskName = 'WhimboxAutoUpdate'
    $trigger = New-ScheduledTaskTrigger -Daily -At $Time
    $action = New-ScheduledTaskAction -Execute $executable -Argument $argumentLine -WorkingDirectory $PSScriptRoot
    $principal = New-ScheduledTaskPrincipal -UserId $resolvedUser -LogonType S4U -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host "OK: '$taskName' registered for daily $Time as $resolvedUser with highest privileges."
    Write-Host "App directory: $AppDir"
    Write-Host "Data and logs: $DataDir"
    Write-Host "Remove: Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
    Write-Host "Run now: Start-ScheduledTask -TaskName '$taskName'"
} catch {
    $exitCode = 1
    Write-Host "[ERROR] $($_.Exception.Message)"
} finally {
    if ($PauseOnExit) {
        Write-Host ''
        try { Read-Host 'Press Enter to close' | Out-Null } catch {}
    }
}
exit $exitCode
