param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('\A[0-9]+\.[0-9]+\.[0-9]+\z')]
    [string]$Version,

    [string]$OutputDirectory = 'release-assets'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$out = [System.IO.Path]::GetFullPath((Join-Path $repo $OutputDirectory))
$source = Join-Path $repo 'whimbox_updater.py'
$requiredFiles = @(
    $source,
    (Join-Path $repo 'README.md'),
    (Join-Path $repo 'LICENSE'),
    (Join-Path $repo 'CHANGELOG.md'),
    (Join-Path $repo 'requirements-build.txt'),
    (Join-Path $repo '.github/workflows/windows-release.yml')
)
$entryNames = @(
    'run_updater.bat', 'update_now.bat', 'check_only.bat', 'rollback.bat',
    'subscriptions.bat', 'install_task.bat', 'install_task.ps1'
)
$pyInstallerVersion = '6.16.0'

foreach ($path in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required release file is missing: $path"
    }
}
foreach ($name in $entryNames) {
    if (-not (Test-Path -LiteralPath (Join-Path $repo $name) -PathType Leaf)) {
        throw "Required entry point is missing: $name"
    }
}

$sourceText = [System.IO.File]::ReadAllText($source)
$versionMatch = [regex]::Match($sourceText, '(?m)^VERSION\s*=\s*["''](?<version>[^"'']+)["'']\s*$')
if (-not $versionMatch.Success) {
    throw 'Could not read VERSION from whimbox_updater.py'
}
if ($versionMatch.Groups['version'].Value -ne $Version) {
    throw "Requested version $Version does not match source VERSION $($versionMatch.Groups['version'].Value)"
}

$installedPyInstaller = [string](& python -c 'import PyInstaller; print(PyInstaller.__version__)' 2>$null | Select-Object -Last 1)
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($installedPyInstaller) -or
    $installedPyInstaller.Trim() -ne $pyInstallerVersion) {
    throw "Expected PyInstaller $pyInstallerVersion; found '$installedPyInstaller'"
}

function New-ReleaseArchive([string]$Directory, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Force }
    # Compress-Archive skips hidden files/directories, including .github on some hosts.
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $Directory, $Destination, [System.IO.Compression.CompressionLevel]::Optimal, $false)
    $archive = [System.IO.Compression.ZipFile]::OpenRead($Destination)
    try {
        $expected = @(Get-ChildItem -LiteralPath $Directory -File -Recurse -Force | ForEach-Object {
            [System.IO.Path]::GetRelativePath($Directory, $_.FullName).Replace('\', '/')
        })
        $actual = @($archive.Entries | Where-Object { -not $_.FullName.EndsWith('/') } | ForEach-Object {
            $_.FullName.Replace('\', '/')
        })
        if (@(Compare-Object -ReferenceObject $expected -DifferenceObject $actual -CaseSensitive).Count -ne 0) {
            throw "Archive contents do not match staged files: $Destination"
        }
    } finally {
        $archive.Dispose()
    }
}

New-Item -ItemType Directory -Path $out -Force | Out-Null
$work = Join-Path $out '.build'
$dist = Join-Path $work 'dist'
New-Item -ItemType Directory -Path $work -Force | Out-Null
& python -m PyInstaller --clean --noconfirm --onefile --console --name 'whimbox-updater' `
    --distpath $dist --workpath (Join-Path $work 'work') --specpath $work $source
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$builtExe = Join-Path $dist 'whimbox-updater.exe'
if (-not (Test-Path -LiteralPath $builtExe -PathType Leaf)) {
    throw 'PyInstaller did not produce whimbox-updater.exe'
}

$portable = Join-Path $work 'portable'
if (Test-Path -LiteralPath $portable) { Remove-Item -LiteralPath $portable -Recurse -Force }
New-Item -ItemType Directory -Path $portable | Out-Null
Copy-Item -LiteralPath $builtExe -Destination (Join-Path $portable 'whimbox-updater.exe')
foreach ($name in @('README.md', 'LICENSE', 'CHANGELOG.md') + $entryNames) {
    Copy-Item -LiteralPath (Join-Path $repo $name) -Destination $portable
}
$portableZip = Join-Path $out "whimbox-updater-v$Version-windows-x64.zip"
New-ReleaseArchive -Directory $portable -Destination $portableZip

$sourceStage = Join-Path $work 'source'
if (Test-Path -LiteralPath $sourceStage) { Remove-Item -LiteralPath $sourceStage -Recurse -Force }
New-Item -ItemType Directory -Path $sourceStage | Out-Null
function Copy-ReleaseFile([string]$FullName) {
    $relative = [System.IO.Path]::GetRelativePath($repo, $FullName)
    $destination = Join-Path $sourceStage $relative
    New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $FullName -Destination $destination
}

# Root source/documentation and launchers are copied by extension allowlist only.
Get-ChildItem -LiteralPath $repo -File | Where-Object {
    $_.Extension -in @('.py', '.md', '.bat', '.ps1')
} | ForEach-Object { Copy-ReleaseFile $_.FullName }
Copy-ReleaseFile (Join-Path $repo 'LICENSE')
Copy-ReleaseFile (Join-Path $repo 'requirements-build.txt')

# Include tests, tools and workflow sources, excluding Python caches and generated data.
foreach ($directory in @('tests', 'tools')) {
    $path = Join-Path $repo $directory
    if (Test-Path -LiteralPath $path -PathType Container) {
        Get-ChildItem -LiteralPath $path -File -Recurse -Force | Where-Object {
            $_.FullName -notmatch '[\\/](__pycache__|_data|\.git)[\\/]' -and
            $_.Extension -in @('.py', '.md', '.bat', '.ps1', '.txt')
        } | ForEach-Object { Copy-ReleaseFile $_.FullName }
    }
}
$workflowDirectory = Join-Path $repo '.github'
if (Test-Path -LiteralPath $workflowDirectory -PathType Container) {
    Get-ChildItem -LiteralPath $workflowDirectory -File -Recurse -Force | Where-Object {
        $_.FullName -notmatch '[\\/](__pycache__|_data|\.git)[\\/]' -and
        $_.Extension -in @('.yml', '.yaml')
    } | ForEach-Object { Copy-ReleaseFile $_.FullName }
}

$sourceZip = Join-Path $out "whimbox-updater-v$Version-source.zip"
New-ReleaseArchive -Directory $sourceStage -Destination $sourceZip

$standaloneExe = Join-Path $out "whimbox-updater-v$Version.exe"
Copy-Item -LiteralPath $builtExe -Destination $standaloneExe -Force
$checksumFile = Join-Path $out 'SHA256SUMS.txt'
$releaseFiles = @($standaloneExe, $portableZip, $sourceZip)
$checksumLines = foreach ($file in $releaseFiles) {
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash *$([System.IO.Path]::GetFileName($file))"
}
[System.IO.File]::WriteAllLines($checksumFile, $checksumLines, [System.Text.Encoding]::ASCII)

Remove-Item -LiteralPath $work -Recurse -Force
Write-Host "Created release assets in $out"
Get-ChildItem -LiteralPath $out -File | ForEach-Object {
    Write-Host ("  {0} ({1:N0} bytes)" -f $_.Name, $_.Length)
}
