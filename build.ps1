# Build banner app: PyInstaller onedir + NSIS installer
# Usage (PowerShell 7 recommended, 5.1 works too):
#   pwsh -NoProfile -File build.ps1 [-SkipExe] [-PythonExe <path>]
#   powershell -ExecutionPolicy Bypass -File build.ps1 [-SkipExe] [-PythonExe <path>]
# The script is ASCII-only on purpose: Windows PowerShell 5.1 reads non-BOM
# UTF-8 scripts as ANSI, which breaks on non-ASCII text.
param(
    [switch]$SkipExe,
    # Prefer this project's environment; allow an explicit interpreter override.
    [string]$PythonExe = "",
    [ValidateRange(1, 1024)]
    [double]$MaxInstallerMB = 20
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
Write-Host ("==> Shell: PowerShell {0}" -f $PSVersionTable.PSVersion)

$python = $PythonExe
if (-not $SkipExe -and -not $python) {
    $localPython = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $localPython) {
        $python = $localPython
    } else {
        $python = (Get-Command python -ErrorAction Stop).Source
    }
}
$makensis = Join-Path $root "tools\nsis\nsis-3.10\makensis.exe"

if (-not $SkipExe) {
    if (-not (Test-Path $python)) {
        throw "Python not found: $python (pass -PythonExe <path> to override)"
    }
    Write-Host ("==> Python: {0}" -f $python)
    Write-Host "==> PyInstaller build"
    & $python -m PyInstaller Banner.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
} else {
    Write-Host "==> Skip PyInstaller (reuse dist\Banner)"
}
if (-not (Test-Path (Join-Path $root "dist\Banner\Banner.exe"))) {
    throw "Missing dist\Banner\Banner.exe; run without -SkipExe first"
}

# Verify the frozen runtime before producing an installer. Isolate all app data.
$probeRoot = Join-Path $root ("build\runtime-probe-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $probeRoot -Force | Out-Null
$probeInfo = New-Object System.Diagnostics.ProcessStartInfo
$probeInfo.FileName = Join-Path $root "dist\Banner\Banner.exe"
$probeInfo.Arguments = "--check-runtime"
$probeInfo.UseShellExecute = $false
$probeInfo.CreateNoWindow = $true
$probeInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$probeInfo.EnvironmentVariables["LOCALAPPDATA"] = $probeRoot
$probe = [System.Diagnostics.Process]::Start($probeInfo)
if (-not $probe.WaitForExit(30000)) {
    $probe.Kill()
    throw "Frozen runtime check timed out"
}
$probeReport = Join-Path $probeRoot "Banner\runtime-check.json"
if ($probe.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $probeReport)) {
    if (Test-Path -LiteralPath $probeReport) { Get-Content -LiteralPath $probeReport }
    throw "Frozen runtime check failed"
}
$probeResult = Get-Content -LiteralPath $probeReport -Raw | ConvertFrom-Json
if (-not $probeResult.ok) { throw "Frozen runtime check reported a failure" }
Copy-Item -LiteralPath $probeReport -Destination (Join-Path $root "dist\runtime-check.json") -Force
Write-Host ("==> Runtime check: {0} checks passed" -f $probeResult.checks.Count)

if (-not (Test-Path $makensis)) { throw "makensis not found: $makensis" }

# NSIS needs a UTF-8 BOM for Unicode scripts (Chinese UI text); repair if an
# editor saved it without BOM.
$nsi = Join-Path $root "installer.nsi"
$bytes = [IO.File]::ReadAllBytes($nsi)
$hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
if (-not $hasBom) {
    $text = [Text.Encoding]::UTF8.GetString($bytes)
    [IO.File]::WriteAllText($nsi, $text, (New-Object Text.UTF8Encoding($true)))
    Write-Host "==> Added UTF-8 BOM to installer.nsi"
}

New-Item -ItemType Directory -Force -Path (Join-Path $root "dist\installer") | Out-Null
Write-Host "==> NSIS installer"
& $makensis /V2 "/DPROJECTDIR=$root" $nsi
if ($LASTEXITCODE -ne 0) { throw "makensis failed ($LASTEXITCODE)" }

Get-ChildItem (Join-Path $root "dist\installer") | ForEach-Object {
    Write-Host ("Output: {0}  ({1:N1} MB)" -f $_.FullName, ($_.Length / 1MB))
}

$bundleBytes = (Get-ChildItem -LiteralPath (Join-Path $root "dist\Banner") -File -Recurse |
    Measure-Object Length -Sum).Sum
$installers = @(Get-ChildItem -LiteralPath (Join-Path $root "dist\installer") -Filter "*.exe" -File)
$report = [ordered]@{
    bundleBytes = $bundleBytes
    maxInstallerMB = $MaxInstallerMB
    installers = @($installers | ForEach-Object { @{ name = $_.Name; bytes = $_.Length } })
}
$report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $root "dist\build-size.json") -Encoding UTF8
foreach ($installer in $installers) {
    if ($installer.Length -gt ($MaxInstallerMB * 1MB)) {
        throw ("Installer exceeds {0} MB: {1}. See dist\build-size.json" -f $MaxInstallerMB, $installer.Name)
    }
}
Write-Host ("Bundle size: {0:N2} MB; installer budget: {1} MB" -f ($bundleBytes / 1MB), $MaxInstallerMB)
