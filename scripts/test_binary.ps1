param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Report
)

# Test a relocated bundle with no Python/venv paths in the environment.
# Run in a child PowerShell session: environment changes are process-local.
$ErrorActionPreference = 'Stop'
$binary = (Resolve-Path $Executable).Path
$reportPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Report)
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ('glidepath-binary-test-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    # A standalone exe needs its sibling libraries; a onefile exe is copied alone.
    if ((Split-Path (Split-Path $binary -Parent) -Leaf) -like '*.dist') {
        Copy-Item -Recurse (Join-Path (Split-Path $binary -Parent) '*') $temporary
    } else {
        Copy-Item $binary $temporary
    }
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    Remove-Item Env:PYTHONPATH, Env:PYTHONHOME, Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:QT_QPA_FONTDIR = Join-Path $env:SystemRoot 'Fonts'
    $temporaryReport = Join-Path $temporary 'smoke.json'
    $process = Start-Process -FilePath (Join-Path $temporary (Split-Path $binary -Leaf)) `
        -ArgumentList @('--smoke-test', ('"' + $temporaryReport + '"')) `
        -WorkingDirectory $temporary -PassThru
    if (-not $process.WaitForExit(120000)) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F | Out-Null
        throw 'The compiled app smoke test timed out after 120 seconds.'
    }
    $process.Refresh()
    if (-not (Test-Path $temporaryReport)) {
        throw "The compiled app produced no smoke report (exit $($process.ExitCode))."
    }
    Copy-Item $temporaryReport $reportPath -Force
    $result = Get-Content $reportPath -Raw | ConvertFrom-Json
    if ($process.ExitCode -ne 0 -or $result.status -ne 'passed') {
        throw "Compiled app check failed: $(Get-Content $reportPath -Raw)"
    }
    Write-Output "Glidepath $($result.version): compiled app checks passed."
} finally {
    Remove-Item -Recurse -Force $temporary
}
