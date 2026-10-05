param([string]$Project = '')
$ErrorActionPreference = 'Stop'
$taskNode = $null
$taskCandidates = @(
    (Join-Path $PSScriptRoot 'node.exe'),
    (Join-Path (Split-Path -Parent $PSScriptRoot) 'node.exe'),
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe')
)
foreach ($taskCandidate in $taskCandidates) {
    if (Test-Path -LiteralPath $taskCandidate -PathType Leaf) { $taskNode = $taskCandidate; break }
}
if (-not $taskNode) {
    $taskCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($taskCommand) { $taskNode = $taskCommand.Source }
}
if (-not $taskNode) { throw '未找到 Node.js。可将 Node.js 22+ 的 node.exe 放在此目录；普通排版也可直接打开 编辑画布.html。' }
if (-not $Project) { $Project = Join-Path (Split-Path -Parent $PSScriptRoot) '编辑画布.html' }
if ($Project) {
    $taskProject = (Resolve-Path -LiteralPath $Project).Path
    $taskArguments = @((Join-Path $PSScriptRoot 'hub.mjs'), '--open', $taskProject)
    if ($env:CHARACTER_CANVAS_NO_BROWSER -eq '1') { $taskArguments += '--no-browser' }
    & $taskNode @taskArguments
} else {
    & $taskNode (Join-Path $PSScriptRoot 'hub.mjs')
}
if ($LASTEXITCODE -ne 0) { throw '编辑器未能启动。' }
