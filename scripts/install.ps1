#Requires -Version 5.1
<#
.SYNOPSIS
    Render GRIP's Windows Task Scheduler jobs from *.task.xml.template and register them.

.DESCRIPTION
    The Windows renderer half of the cross-platform scheduler (#23). It parallels the
    macOS launchd path in scripts/install.sh: one job source of truth
    (config\windows\*.task.xml.template), placeholders substituted at install time,
    ZERO hardcoded operator paths.

    Substitutions (matching docs/cross-platform-scheduler.md):
        __GRIP_HOME__   -> $env:CLAUDE_DIR      (fallback: "$env:USERPROFILE\.claude")
        __GRIP_PYTHON__ -> (Get-Command python).Source  (fallback: python3)

    Idempotent: schtasks /create /f overwrites an existing task, so re-running
    converges to the same registered state (no duplicate tasks).

    The registered task name is the template's file stem, e.g.
    grip-heartbeat.task.xml.template -> a task named "grip-heartbeat".

.PARAMETER DryRun
    Render each template to $env:TEMP and print the path WITHOUT registering the
    task. Use to inspect the substituted XML before it touches Task Scheduler.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -DryRun

.NOTES
    A>> (AndreTheart) validates on a real Windows host (a
    teammate's always-on server is the target exit-node). Windows behaviour cannot be
    validated on macOS/CI, so this renderer is authored correct-by-construction;
    execution-validation on Windows is the exit gate. Portability (no hardcoded
    operator path/username) is enforced by tests/test_no_hardcoded_operator_paths.py.
#>
[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Resolve-ClaudeDir {
    if ($env:CLAUDE_DIR) { return $env:CLAUDE_DIR }
    return (Join-Path $env:USERPROFILE ".claude")
}

function Resolve-Python {
    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw "Python not found. Install Python 3.9+ and ensure it is on PATH."
}

function Get-TemplateDir([string] $ClaudeDir) {
    foreach ($base in @($ClaudeDir, (Join-Path $PSScriptRoot ".."))) {
        $dir = Join-Path $base "config\windows"
        if (Test-Path $dir) { return (Resolve-Path $dir).Path }
    }
    throw "No config\windows template directory found under '$ClaudeDir' or '$PSScriptRoot\..'."
}

function Convert-Template([string] $TemplatePath, [string] $ClaudeDir, [string] $Python) {
    $xml = Get-Content -Path $TemplatePath -Raw
    return $xml.Replace("__GRIP_HOME__", $ClaudeDir).Replace("__GRIP_PYTHON__", $Python)
}

function Register-RenderedTask([string] $TaskName, [string] $RenderedXml, [bool] $DryRunMode) {
    $out = Join-Path $env:TEMP ("{0}.xml" -f $TaskName)
    # schtasks /xml requires UTF-16 (the templates declare encoding="UTF-16").
    # Set-Content -Encoding Unicode writes UTF-16 LE with a BOM, which schtasks accepts.
    Set-Content -Path $out -Value $RenderedXml -Encoding Unicode
    if ($DryRunMode) {
        Write-Host "  [dry-run] $TaskName rendered -> $out (not registered)"
        return
    }
    schtasks /create /tn $TaskName /xml "$out" /f | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "schtasks /create failed for '$TaskName' (exit $LASTEXITCODE)."
    }
    Remove-Item -Path $out -ErrorAction SilentlyContinue
    Write-Host "  installed: $TaskName"
}

# --- main ----------------------------------------------------------------
$ClaudeDir   = Resolve-ClaudeDir
$Python      = Resolve-Python
$TemplateDir = Get-TemplateDir $ClaudeDir
$templates   = @(Get-ChildItem -Path $TemplateDir -Filter "*.task.xml.template" -File)

Write-Host "GRIP Windows scheduler install"
Write-Host "  CLAUDE_DIR : $ClaudeDir"
Write-Host "  python     : $Python"
Write-Host "  templates  : $($templates.Count) in $TemplateDir"

if ($templates.Count -eq 0) {
    Write-Warning "No *.task.xml.template files found - nothing to install."
    return
}

foreach ($tpl in $templates) {
    $taskName = $tpl.Name -replace '\.task\.xml\.template$', ''
    $rendered = Convert-Template $tpl.FullName $ClaudeDir $Python
    Register-RenderedTask $taskName $rendered $DryRun.IsPresent
}

Write-Host ""
Write-Host "Done. Verify:  schtasks /query /tn <name>   (e.g. grip-heartbeat)"
Write-Host "Remove:        schtasks /delete /tn <name> /f"
