# Fixed local entry point. Production agents do not inspect implementation files.
$ErrorActionPreference = 'Stop'
$runtimeBase = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.cache/codex-runtimes/codex-primary-runtime/dependencies'
$pythonRuntime = Join-Path $runtimeBase 'python/python.exe'
if (-not (Test-Path -LiteralPath $pythonRuntime -PathType Leaf)) {
    $pythonCommand = Get-Command python3, python -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch 'WindowsApps' } | Select-Object -First 1
    if (-not $pythonCommand) { throw 'Python runtime unavailable. Use the environment-provided Python path to run scripts/sheet_flow.py.' }
    $pythonRuntime = $pythonCommand.Source
}
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& $pythonRuntime -B -X utf8 (Join-Path $PSScriptRoot 'sheet_flow.py') @args
exit $LASTEXITCODE
