# grip-black-october.ps1 -- check one Windows machine against the October 2026
# ("Black October") security floors, then apply each fix only after a typed yes.
#
# The Windows twin of bin/grip-black-october (bash, macOS and Linux), GRIP#8858.
# It keeps the same item names, statuses and JSON shape, so a Windows report
# lines up with a Mac or a Linux report row by row.
#
# Standalone on purpose: Windows PowerShell 5.1 or PowerShell 7, no GRIP imports;
# copy the file as it is. The file is ASCII only, so Windows PowerShell 5.1 reads
# it the same way with or without a byte-order mark.
#
# Run it from a console window (not the PowerShell ISE):
#   powershell -NoProfile -ExecutionPolicy Bypass -File grip-black-october.ps1 <command>
#
#   check [-Network] [-Json]  every read-only check (the default command); -Json prints JSON
#   help                      this text
#   version                   the version of this script
#
# check reads only: it writes no file, folder, setting or certificate store. The
# trust rows read reviewed copies of the incident page's two lists from the work
# folder, and use a copy only when its sha256 equals the pin below.
#
# check -Network also connects each HTTPS client on the PC to the incident page and
# its 14 test sites, one connection at a time, working in one new folder under TEMP
# that it removes at the end. Without reviewed copies it fetches the two lists into
# memory and saves nothing. Windows itself may cache certificates while it runs, and
# the Windows Update search contacts Windows Update or WSUS. It reads six certificate
# stores before the first connection and after every one, and when an incident
# certificate appears, every later connection stops.
#
# Environment variables (each has a safe default):
#   GBO_CODE_DIRS   the folders the .env check searches: full paths separated by ';'
#   GBO_PYTHONS     more Pythons to ask: full paths to python.exe separated by ';'
#   GBO_WORK_DIR    the work folder (default %USERPROFILE%\.grip-black-october)
#   GBO_NO_MAIN=1   load the functions without running a command (for tests)

# No param() block: PowerShell never binds a word that starts with a dash (-h,
# --help, -Network) to a positional parameter, so the script reads every
# argument from $args itself, in the order it was typed.

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$GboVersion = '2026.11.05'
$GboStatuses = @('ok', 'at risk', 'unknown', 'question', 'info', 'n/a')
# Lists are made with ::new(), never New-Object: New-Object hands back the list wrapped
# in a PSObject, and @() over a wrapped generic list throws "Argument types do not
# match" (measured 2026-11-05 in 5.1 and 7).
$GboRows = [System.Collections.Generic.List[object]]::new()
# The two incident lists for this run, by file name. check -Network fills it before
# any check runs; in a plain check it stays empty, and the trust rows read the
# reviewed copies in the work folder.
$GboPinnedCache = @{}

# ---- floors (copied from bin/grip-black-october; a parity test keeps them equal) --
# Each vendor source is the one the bash script names beside the same floor.
$FLOOR_NODE_26 = '26.12.1'      # nodejs.org October 2026 security releases
$FLOOR_NODE_24 = '24.22.1'      #   (CVE-2026-111497, Brotli decoder OOB write)
$FLOOR_NODE_22 = '22.23.4'
$FLOOR_BROTLI = '1.2.1'         # github.com/google/brotli/releases/tag/v1.2.1
$FLOOR_BUN = '1.4.4'            # github.com/oven-sh/bun/issues/46352
$FLOOR_CLAUDE = '2.1.308'       # README check 4 (marker control=3 fix=3 at 2.1.308)
$FLOOR_PIP = '26.2.2'           # pip.pypa.io/en/stable/news/ (2026-10-20)
$FLOOR_UV = '0.13.5'            # github.com/astral-sh/uv/releases/tag/0.13.5
$FLOOR_CERTIFI = '2026.10.19'   # certifi git: "Remove emsign" 2026-10-18
$FLOOR_TAILSCALE = '1.104.2'    # tailscale.com/security-bulletins TS-2026-013
$FLOOR_CHROME = '156'           # chromereleases.googleblog.com (353 fixes)
# Edge, new for Windows: learn.microsoft.com/en-us/deployedge/microsoft-edge-relnotes-security,
# "October 22, 2026": Stable 156.0.4314.42 "incorporates the latest Security Updates of the
# Chromium project", the first Stable 156 (read 2026-11-03).
$FLOOR_EDGE = '156.0.4314.42'
# .NET and PowerShell 7, new for Windows: MSRC lists the Brotli decoder bug,
# CVE-2026-111497, as "Out-of-bounds write in .NET allows an unauthorized attacker to
# elevate privileges over a network" (api.msrc.microsoft.com, released 2026-10-25,
# updated 2026-11-01). The fixed runtimes come from Microsoft's .NET release metadata
# (dotnetcli.blob.core.windows.net/dotnet/release-metadata/<channel>/releases.json:
# each released 2026-10-25 with that CVE in its list), and the fixed PowerShell
# releases from MSRC's affected-product list (all read 2026-11-05).
$FLOOR_DOTNET_8 = '8.0.33'
$FLOOR_DOTNET_9 = '9.0.22'
$FLOOR_DOTNET_10 = '10.0.14'
$FLOOR_PWSH_74 = '7.4.22'
$FLOOR_PWSH_75 = '7.5.13'
$FLOOR_PWSH_76 = '7.6.9'
# servers: bash's floors; each row's evidence names the advisory, as in bash
$FLOOR_PHP_85 = '8.5.12'
$FLOOR_PHP_84 = '8.4.27'
$FLOOR_PHP_83 = '8.3.36'
$FLOOR_PHP_82 = '8.2.35'
$FLOOR_RUBY_40 = '4.0.9'
$FLOOR_RUBY_34 = '3.4.12'
$FLOOR_RUBY_33 = '3.3.13'
$FLOOR_NEXT_16 = '16.4.1'
$FLOOR_NEXT_15 = '15.5.28'
$FLOOR_SHARP = '0.35.5'
$FLOOR_STORYBOOK_8 = '8.6.19'
$FLOOR_STORYBOOK_9 = '9.1.21'
$FLOOR_STORYBOOK_10 = '10.6.2'
$FLOOR_PILLOW = '13.0.0'

# ---- the incident page's lists, pinned by sha256 after human review (as in bash) --------
# Trust lists are not embedded. A copy is used only when its sha256 equals the pin;
# a mismatch means the list changed: review it, then re-pin.
$INCIDENT_BASE = 'https://emudhra.we-have-a-problem.com'
$ROOTS_JSON_SHA256 = '119f32cd81a6b5a4e3c4c2df5fe07daa90dc43cafa123a62f0394f1895d92380'
$INTERMEDIATES_JSON_SHA256 = 'f67b3abe9b4879f4e91874ed041beccd71257f1db41c588c724ccf12aa90c59e'
$EXPECTED_INTERMEDIATES = 16
# The two lists by file name, each with its pin.
$GboPinnedLists = [ordered]@{ 'roots.json' = $ROOTS_JSON_SHA256; 'impacted-intermediates.json' = $INTERMEDIATES_JSON_SHA256 }

# ---- output ---------------------------------------------------------------------
# [Console]::Out and [Console]::Error behave the same in 5.1 and 7, and keep the
# JSON on stdout free of anything PowerShell's own formatting would add.
function say([string]$Text = '') { [Console]::Out.WriteLine($Text) }
function warn([string]$Text = '') { [Console]::Error.WriteLine($Text) }

# ---- versions (bash: vge, status_for) --------------------------------------------
# Test-GboVge A B: $true when version A >= version B, exactly as bash's vge decides.
# One leading lowercase "v" is dropped; every other character that is not a digit,
# a dot or a newline becomes a dot; the first six dot-separated components are
# compared as numbers, an empty one counting as 0. A component that is not a
# number bash's test can read (too long) is skipped, as bash skips it.
function Test-GboVge {
    param([string]$A, [string]$B)
    $fa = ((($A -creplace '^v', '') -creplace '[^0-9.\n]', '.') + '.').Split('.')
    $fb = ((($B -creplace '^v', '') -creplace '[^0-9.\n]', '.') + '.').Split('.')
    for ($i = 0; $i -lt 6; $i++) {
        $sa = Get-GboComponent $fa $i
        $sb = Get-GboComponent $fb $i
        $ia = [long]0
        $ib = [long]0
        $style = [Globalization.NumberStyles]::None
        $culture = [Globalization.CultureInfo]::InvariantCulture
        if (-not [long]::TryParse($sa, $style, $culture, [ref]$ia)) { continue }
        if (-not [long]::TryParse($sb, $style, $culture, [ref]$ib)) { continue }
        if ($ia -gt $ib) { return $true }
        if ($ia -lt $ib) { return $false }
    }
    return $true
}

function Get-GboComponent([string[]]$Fields, [int]$Index) {
    if ($Index -lt $Fields.Length -and $Fields[$Index]) { return $Fields[$Index] }
    return '0'
}

# Get-GboStatusFor VERSION FLOOR -> 'ok' or 'at risk'; 'unknown' when VERSION is empty.
function Get-GboStatusFor([string]$Version, [string]$Floor) {
    if (-not $Version) { return 'unknown' }
    if (Test-GboVge $Version $Floor) { return 'ok' }
    return 'at risk'
}

# ---- rows and their two outputs (bash: row, emit_rows) ---------------------------
# Evidence is normalised on the way in: carriage returns and NUL characters, which
# Windows tools print, are removed. Every other character is kept as it is.
function ConvertTo-GboEvidence([string]$Text) {
    return (($Text -replace "`r", '') -replace "`0", '')
}

function Add-GboRow([string]$Item, [string]$Status, [string]$Evidence) {
    if ($GboStatuses -cnotcontains $Status) {
        throw "internal error: status '$Status' is not one of: $($GboStatuses -join ', ')"
    }
    $GboRows.Add([pscustomobject]@{
        item = $Item; status = $Status; evidence = (ConvertTo-GboEvidence $Evidence)
    })
}

# The JSON is built by hand: ConvertTo-Json escapes differently in 5.1 and 7, and
# the key order must not depend on the version. Every character below U+0020 and
# every character above U+007E is escaped, so the output is valid JSON and ASCII.
function ConvertTo-GboJsonString([string]$Text) {
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $Text.ToCharArray()) {
        $code = [int]$ch
        if ($code -eq 34) { [void]$sb.Append('\"') }
        elseif ($code -eq 92) { [void]$sb.Append('\\') }
        elseif ($code -eq 9) { [void]$sb.Append('\t') }
        elseif ($code -eq 10) { [void]$sb.Append('\n') }
        elseif ($code -lt 32 -or $code -gt 126) { [void]$sb.Append(('\u{0:x4}' -f $code)) }
        else { [void]$sb.Append($ch) }
    }
    return $sb.ToString()
}

function Get-GboUtcStamp {
    $format = "yyyy'-'MM'-'dd'T'HH':'mm':'ss'Z'"
    return [DateTime]::UtcNow.ToString($format, [Globalization.CultureInfo]::InvariantCulture)
}

function Write-GboRows([bool]$AsJson, [bool]$NetworkRun) {
    if (-not $AsJson) {
        say 'item | status | evidence'
        say '--- | --- | ---'
        foreach ($r in $GboRows) { say ('{0} | {1} | {2}' -f $r.item, $r.status, $r.evidence) }
        return
    }
    $items = foreach ($r in $GboRows) {
        '{"item":"' + (ConvertTo-GboJsonString $r.item) + '","status":"' + (ConvertTo-GboJsonString $r.status) +
            '","evidence":"' + (ConvertTo-GboJsonString $r.evidence) + '"}'
    }
    $network = 'false'
    if ($NetworkRun) { $network = 'true' }
    say ('{"tool":"grip-black-october","version":"' + (ConvertTo-GboJsonString $GboVersion) +
        '","os":"Windows_NT","generated_utc":"' + (Get-GboUtcStamp) + '","network":' + $network +
        ',"items":[' + (@($items) -join ',') + ']}')
}

# ---- running other programs ---------------------------------------------------------
# ConvertTo-GboArgument: quote one argument by the rules the Microsoft C runtime
# and CommandLineToArgvW use to split a command line, so the program receives
# exactly $Value. Backslashes are literal except before a double quote.
function ConvertTo-GboArgument([string]$Value) {
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('"')
    $slashes = 0
    foreach ($ch in $Value.ToCharArray()) {
        $code = [int]$ch
        if ($code -eq 92) { $slashes++; continue }
        if ($code -eq 34) { [void]$sb.Append([string]::new([char]92, 2 * $slashes + 1)) }
        else { [void]$sb.Append([string]::new([char]92, $slashes)) }
        [void]$sb.Append($ch)
        $slashes = 0
    }
    [void]$sb.Append([string]::new([char]92, 2 * $slashes))
    [void]$sb.Append('"')
    return $sb.ToString()
}

# A full path: a drive letter with a backslash or slash, or a UNC path. A bare
# name would let Windows search the CURRENT folder first, so a program planted
# in whatever folder the user runs this from could run in its place.
function Test-GboFullPath([string]$Path) {
    return ($Path -match '^(?:[A-Za-z]:[\\/]|\\\\)')
}

function Stop-GboProcessTree([int]$ProcessId) {
    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $taskkill
    $psi.Arguments = "/T /F /PID $ProcessId"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $killer = [System.Diagnostics.Process]::Start($psi)
    [void]$killer.WaitForExit(10000)
}

# Set-GboChildVariable: give one child process one environment variable, spelt
# exactly as given. The dictionary ignores case, so setting a name it already
# holds (git_terminal_prompt, say) would keep the old spelling, and a child such
# as git, which reads names case by case, would then miss it. Remove, then add.
function Set-GboChildVariable($StartInfo, [string]$Name, [string]$Value) {
    [void]$StartInfo.Environment.Remove($Name)
    $StartInfo.Environment[$Name] = $Value
}

# Every program this script runs starts in the system folder, unless its caller names
# another (the network probe names its own new folder). The folder the script was started
# in may hold files that a program loads as its own code: a Python imports a certifi.py or
# a pip package from it, and uv reads a uv.toml there that can change where it downloads
# from (measured 2026-11-05: a planted certifi.py and pip package both ran). Only
# administrators can put a file in the system folder.
$GboSafeFolder = [Environment]::SystemDirectory

# Invoke-GboNative: run one program by full path with a time limit and return
# @{ ExitCode; TimedOut; StdOut; StdErr }. stdin is closed at once, so nothing can
# wait for a keypress; on a timeout the whole process tree is killed and ExitCode
# is $null. Extra environment variables apply to this child only (the
# Environment property keeps each name's case; the older EnvironmentVariables
# property lower-cases names, which a case-sensitive child such as git reads
# differently). Every child gets PYTHONDONTWRITEBYTECODE=1, so a Python asked a
# version question writes no bytecode cache. WorkingDirectory, when given, is the
# folder the child starts in; otherwise it starts in $GboSafeFolder.
function Invoke-GboNative {
    param(
        [string]$Path,
        [string[]]$ArgumentList = @(),
        [int]$TimeoutSeconds = 30,
        [hashtable]$Environment = @{},
        [string]$OutputEncoding = 'utf8',
        [string]$WorkingDirectory = ''
    )
    if (-not (Test-GboFullPath $Path)) { throw "internal error: Invoke-GboNative needs a full path, got '$Path'" }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Path
    if ($WorkingDirectory) {
        # A relative start folder would be read against the folder this script started in.
        if (-not (Test-GboFullPath $WorkingDirectory)) { throw "internal error: a start folder must be a full path, got '$WorkingDirectory'" }
        $psi.WorkingDirectory = $WorkingDirectory
    } else {
        # An empty start folder would mean the folder this script started in, so refuse instead.
        if (-not (Test-GboFullPath $GboSafeFolder)) { throw 'internal error: no system folder to start programs in' }
        $psi.WorkingDirectory = $GboSafeFolder
    }
    $psi.Arguments = (@($ArgumentList | ForEach-Object { ConvertTo-GboArgument $_ }) -join ' ')
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $encoding = New-Object System.Text.UTF8Encoding($false)
    if ($OutputEncoding -eq 'unicode') { $encoding = [System.Text.Encoding]::Unicode }
    $psi.StandardOutputEncoding = $encoding
    $psi.StandardErrorEncoding = $encoding
    Set-GboChildVariable $psi 'PYTHONDONTWRITEBYTECODE' '1'
    foreach ($key in @($Environment.Keys)) { Set-GboChildVariable $psi ([string]$key) ([string]$Environment[$key]) }
    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.StandardInput.Close()
    $out = $proc.StandardOutput.ReadToEndAsync()
    $err = $proc.StandardError.ReadToEndAsync()
    $finished = $proc.WaitForExit($TimeoutSeconds * 1000)
    if (-not $finished) {
        Stop-GboProcessTree $proc.Id
        [void]$proc.WaitForExit(5000)
    }
    [void]$out.Wait(5000)
    [void]$err.Wait(5000)
    $exitCode = $null
    if ($finished) { $exitCode = $proc.ExitCode }
    return [pscustomobject]@{
        ExitCode = $exitCode
        TimedOut = (-not $finished)
        StdOut = (Get-GboTaskText $out)
        StdErr = (Get-GboTaskText $err)
    }
}

# A pipe that a surviving grandchild still holds never finishes reading; reading
# .Result then would block for ever, so an unfinished read yields ''.
function Get-GboTaskText($Task) {
    if (-not $Task.IsCompleted) { return '' }
    return (ConvertTo-GboEvidence $Task.Result)
}

# Get-GboEachOnPath NAME: the program Windows would run for NAME from each folder
# on PATH, one per folder, duplicates removed (bash: each_on_path). Only absolute
# PATH entries count: a relative entry would make the answer depend on the folder
# the user happens to be in. Within a folder the PATHEXT order decides, as it
# does for Windows itself, among .COM, .EXE, .BAT and .CMD.
function Get-GboEachOnPath([string]$Name) {
    $runnable = @('.COM', '.EXE', '.BAT', '.CMD')
    $exts = @(($env:PATHEXT -split ';') | Where-Object { $runnable -contains $_.ToUpperInvariant() })
    if ($exts.Count -eq 0) { $exts = $runnable }
    if ($runnable -contains [IO.Path]::GetExtension($Name).ToUpperInvariant()) { $exts = @('') }
    $seen = @{}
    foreach ($entry in ($env:Path -split ';')) {
        $dir = [Environment]::ExpandEnvironmentVariables($entry.Trim().Trim('"'))
        if (-not (Test-GboFullPath $dir)) { continue }
        $found = Get-GboFirstInFolder $dir $Name $exts
        if (-not $found) { continue }
        $key = $found.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $found
    }
}

function Get-GboFirstInFolder([string]$Dir, [string]$Name, [string[]]$Exts) {
    foreach ($ext in $Exts) {
        $candidate = Join-Path $Dir ($Name + $ext.ToLowerInvariant())
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return [IO.Path]::GetFullPath($candidate) }
    }
    return $null
}

# ---- asking, and who is asking ----------------------------------------------------
# Read-GboTypedYes: the literal word yes, nothing else. -ceq compares case by case;
# PowerShell's plain -eq ignores case and would accept YES. A closed stdin (end of
# input) reads as $null and cancels.
function Read-GboTypedYes {
    say ''
    say 'Type yes to run the commands above (anything else cancels):'
    $reply = [Console]::In.ReadLine()
    if ($null -ne $reply -and $reply -ceq 'yes') { return $true }
    say 'cancelled: nothing ran'
    return $false
}

function Test-GboElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---- the checks -----------------------------------------------------------------------
# Invoke-GboCheck ITEM BLOCK: run one check. An exception becomes an 'unknown' row
# carrying the fixed words 'internal error', which the tests look for, so a typo
# can never pass as a quiet 'unknown'. A check reports through rows only; anything
# it leaves on PowerShell's output is dropped, so it can never reach an exit code.
function Invoke-GboCheck([string]$Item, [scriptblock]$Check) {
    try { $null = & $Check }
    catch { Add-GboRow $Item 'unknown' ("internal error in the $Item check: " + $_.Exception.Message) }
}

# Join-GboPath BASE CHILD: Join-Path, except that an empty BASE gives '' instead of
# an error, because an unset variable such as FNM_DIR means "not installed".
function Join-GboPath([string]$Base, [string]$Child) {
    if (-not $Base) { return '' }
    return (Join-Path $Base $Child)
}

# Get-GboVersionedExes ROOT PATTERN RELATIVE: ROOT\<each folder matching PATTERN>\RELATIVE,
# wherever that file exists. A missing ROOT is no error: that tool is not installed.
function Get-GboVersionedExes([string]$Root, [string]$Pattern, [string]$Relative) {
    if (-not $Root -or -not [IO.Directory]::Exists($Root)) { return }
    foreach ($dir in @([IO.Directory]::GetDirectories($Root, $Pattern) | Sort-Object)) {
        $exe = Join-Path $dir $Relative
        if ([IO.File]::Exists($exe)) { $exe }
    }
}

# Every node.exe this machine might run (bash: each node on PATH and under ~/.nvm):
# each one on PATH, every version nvm-windows holds (NVM_HOME, or either default
# folder), every Node image Volta holds (VOLTA_HOME, default LOCALAPPDATA\Volta),
# and fnm's versions when FNM_DIR names its folder.
function Get-GboNodeCandidates {
    $volta = $env:VOLTA_HOME
    if (-not $volta) { $volta = Join-GboPath $env:LOCALAPPDATA 'Volta' }
    $found = @(Get-GboEachOnPath 'node')
    foreach ($root in @($env:NVM_HOME, (Join-GboPath $env:LOCALAPPDATA 'nvm'), (Join-GboPath $env:APPDATA 'nvm'))) {
        $found += @(Get-GboVersionedExes $root 'v*' 'node.exe')
    }
    $found += @(Get-GboVersionedExes (Join-GboPath $volta 'tools\image\node') '*' 'node.exe')
    $found += @(Get-GboVersionedExes (Join-GboPath $env:FNM_DIR 'node-versions') '*' 'installation\node.exe')
    $seen = @{}
    foreach ($path in $found) {
        $key = $path.ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) { $seen[$key] = $true; $path }
    }
}

function Get-GboNodeFloor([string]$Major) {
    $floors = @{ '26' = $FLOOR_NODE_26; '24' = $FLOOR_NODE_24; '22' = $FLOOR_NODE_22 }
    if ($floors.ContainsKey($Major)) { return $floors[$Major] }
    return ''
}

# "${Node}:" needs the braces: in a double-quoted string, "$Node:" would read as a
# drive-qualified variable name and fail to parse.
function Add-GboNodeRow([string]$Node) {
    $answer = Invoke-GboNative -Path $Node -ArgumentList @('-v') -TimeoutSeconds 15
    $version = (@($answer.StdOut -split "`n")[0]).Trim() -creplace '^v', ''
    if (-not $version) { Add-GboRow 'node' 'unknown' "${Node}: does not run"; return }
    $major = $version.Split('.')[0]
    $floor = Get-GboNodeFloor $major
    if ($floor) { Add-GboRow 'node' (Get-GboStatusFor $version $floor) "$Node v$version; floor $floor"; return }
    Add-GboRow 'node' 'unknown' "$Node v$version; no fixed release published for major $major (end of life?)"
}

# bash: check_node
function Invoke-GboCheckNode {
    $nodes = @(Get-GboNodeCandidates)
    if ($nodes.Count -eq 0) { Add-GboRow 'node' 'n/a' 'no node on PATH or under nvm-windows, Volta or fnm'; return }
    foreach ($node in $nodes) { Add-GboNodeRow $node }
}

function Get-GboFirstLine([string]$Text) {
    return (@($Text -split "`n")[0]).Trim()
}

# Resolve-GboLinkedPath PATH: PATH with each folder on the way that is a junction or
# a symbolic link replaced by its target (bash: cd "$(dirname "$p")" && pwd -P), so
# two names for one folder compare equal. ~\.claude is often a junction to ~\.grip,
# and the venv behind both must count once. A UNC path is returned as it is.
function Resolve-GboLinkedPath([string]$Path) {
    $Path = $Path -replace '/', '\'
    if ($Path.StartsWith('\\')) { return $Path }
    $parts = $Path.TrimEnd('\').Split('\')
    $current = $parts[0] + '\'
    for ($i = 1; $i -lt $parts.Length - 1; $i++) {
        $current = Resolve-GboLinkStep (Join-Path $current $parts[$i])
    }
    return (Join-Path $current $parts[$parts.Length - 1])
}

function Resolve-GboLinkStep([string]$Folder) {
    for ($hop = 0; $hop -lt 32; $hop++) {
        $item = Get-Item -LiteralPath $Folder -Force -ErrorAction SilentlyContinue
        if ($null -eq $item -or -not $item.LinkType) { return $Folder }
        $target = [string](@($item.Target)[0])
        if (-not $target) { return $Folder }
        if (-not [IO.Path]::IsPathRooted($target)) { $target = Join-Path (Split-Path -Parent $Folder) $target }
        $Folder = $target
    }
    return $Folder
}

# Every Python worth asking (bash: pythons, unique_pythons): each python.exe on PATH,
# each one the py launcher lists (py -0p), the GRIP and HAL venvs, and any full
# path in GBO_PYTHONS (separated by ';'). Only .exe files: a .bat or .cmd "python"
# (pyenv-win's shims) is never handed Python code, because cmd.exe would re-read
# its quotes first. One interpreter reached by two names counts once.
function Get-GboPythonCandidates {
    $found = @(Get-GboEachOnPath 'python.exe')
    $py = @(Get-GboEachOnPath 'py.exe')
    if ($py.Count -gt 0) { $found += @(Get-GboPyLauncherPaths $py[0]) }
    foreach ($venv in @('.claude', '.grip', '.hal')) {
        $exe = Join-GboPath $env:USERPROFILE "$venv\venv\Scripts\python.exe"  # venv-path-ok(this file is copied as it is to other machines and loads nothing from GRIP, so venv-utils.sh is not available to it; it only reads these venvs to report their pip, certifi and Pillow versions and runs its own one-line probes in them, never GRIP code; GBO_PYTHONS adds any other venv)
        if ($exe -and [IO.File]::Exists($exe)) { $found += $exe }
    }
    if ($env:GBO_PYTHONS) { $found += @($env:GBO_PYTHONS -split ';' | Where-Object { $_ }) }
    $seen = @{}
    foreach ($path in $found) {
        if ([IO.Path]::GetExtension($path) -ne '.exe') { continue }
        $key = (Resolve-GboLinkedPath $path).ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $path
    }
}

# py -0p prints one installed Python per line, with its path last.
function Get-GboPyLauncherPaths([string]$Py) {
    $answer = Invoke-GboNative -Path $Py -ArgumentList @('-0p') -TimeoutSeconds 15
    foreach ($line in ($answer.StdOut -split "`n")) {
        if ($line -match '([A-Za-z]:\\.+?\.exe)\s*$') { $Matches[1] }
    }
}

# Add-GboPythonRows PYTHON: the pip and certifi rows for one interpreter. It returns
# $false, and adds no row, when the interpreter does not run: the Microsoft Store's
# python.exe alias exists but only prints "Python was not found".
function Add-GboPythonRows([string]$Python) {
    $answer = Invoke-GboNative -Path $Python -ArgumentList @('-c', 'import sys;print(sys.version.split()[0])')
    $version = Get-GboFirstLine $answer.StdOut
    if ($version -notmatch '^\d+\.\d+') { return $false }
    $pipAnswer = Invoke-GboNative -Path $Python -ArgumentList @('-m', 'pip', '--version')
    $pipVersion = ''
    if ((Get-GboFirstLine $pipAnswer.StdOut) -match '^pip (\S+)') { $pipVersion = $Matches[1] }
    if ($pipVersion) {
        Add-GboRow 'pip' (Get-GboStatusFor $pipVersion $FLOOR_PIP) "$Python (python $version) pip $pipVersion; floor $FLOOR_PIP"
    } else {
        Add-GboRow 'pip' 'n/a' "$Python (python $version) has no pip"
    }
    $certAnswer = Invoke-GboNative -Path $Python -ArgumentList @('-c', 'import certifi;print(certifi.__version__)')
    $certifi = Get-GboFirstLine $certAnswer.StdOut
    if ($certifi -match '^\d+(\.\d+)+$') {
        Add-GboRow 'certifi' (Get-GboStatusFor $certifi $FLOOR_CERTIFI) "$Python certifi $certifi; floor $FLOOR_CERTIFI"
    }
    return $true
}

function Add-GboUvRows {
    $uvs = @(Get-GboEachOnPath 'uv.exe')
    if ($uvs.Count -eq 0) { Add-GboRow 'uv' 'n/a' 'no uv on PATH'; return }
    foreach ($uv in $uvs) {
        $line = Get-GboFirstLine (Invoke-GboNative -Path $uv -ArgumentList @('--version') -TimeoutSeconds 15).StdOut
        if ($line -notmatch '^uv (\S+)') { Add-GboRow 'uv' 'unknown' "${uv}: does not answer --version"; continue }
        $version = $Matches[1]
        Add-GboRow 'uv' (Get-GboStatusFor $version $FLOOR_UV) "$line at $uv; floor $FLOOR_UV (0.13.5 rejects chains with a compromised intermediate)"
    }
}

# bash: check_python_tools (its Homebrew rows have no Windows counterpart)
function Invoke-GboCheckPythonTools {
    $ran = 0
    foreach ($python in @(Get-GboPythonCandidates)) { if (Add-GboPythonRows $python) { $ran++ } }
    if ($ran -eq 0) { Add-GboRow 'pip' 'n/a' 'no Python that runs (on PATH, from py -0p, in the GRIP or HAL venvs, or in GBO_PYTHONS)' }
    Add-GboUvRows
}

# ---- hygiene (bash: check_hygiene) ----------------------------------------------------
# Get-GboFirewallReadings: the three Windows Firewall profiles as they are in force
# (the active policy, Group Policy included) and as this PC's own setting stores
# them, plus the firewall products Security Center lists. Security Center exists
# only on client editions of Windows; where it cannot be read, SecurityCenter is
# $false and the product list is empty.
function Get-GboFirewallReadings {
    $active = @{}
    foreach ($p in @(Get-NetFirewallProfile -PolicyStore ActiveStore)) { $active[[string]$p.Name] = [string]$p.Enabled }
    $local = @{}
    foreach ($p in @(Get-NetFirewallProfile -PolicyStore PersistentStore)) { $local[[string]$p.Name] = [string]$p.Enabled }
    $products = @()
    $center = $true
    try {
        $products = @(Get-CimInstance -Namespace 'root/SecurityCenter2' -ClassName FirewallProduct |
            ForEach-Object { [string]$_.displayName })
    } catch { $center = $false }
    return [pscustomobject]@{ Active = $active; Local = $local; Products = $products; SecurityCenter = $center }
}

function Get-GboOnOff([string]$Enabled) {
    if ($Enabled -eq 'True') { return 'on' }
    if ($Enabled -eq 'False') { return 'off' }
    return 'not readable'
}

# A profile off in force is 'at risk' unless something explains it. Another firewall
# registered with Security Center usually takes over from Windows Firewall, and a
# profile that is on in the local setting but off in force is held off by Group
# Policy. Both read 'question': neither is this PC's own setting to change.
function Get-GboFirewallVerdict($Reading, [string[]]$Off) {
    if (@($Reading.Products).Count -gt 0) {
        return @('question', '; another firewall is registered, which usually takes over: confirm it covers this network')
    }
    $held = @($Off | Where-Object { $Reading.Local[$_] -eq 'True' })
    if ($held.Count -gt 0) {
        # The brackets matter: PowerShell's comma binds tighter than +, so without
        # them the pieces would become extra list items instead of one string.
        $note = '; ' + ($held -join ', ') + ' on in the local setting but off in force, so Group Policy holds it off: ask whoever manages this PC'
        return @('question', $note)
    }
    return @('at risk', '')
}

# bash: the firewall half of check_hygiene.
function Invoke-GboCheckFirewall {
    try { $reading = Get-GboFirewallReadings }
    catch { Add-GboRow 'firewall' 'unknown' ('could not ask Windows Firewall: ' + $_.Exception.Message); return }
    $names = @('Domain', 'Private', 'Public')
    $off = @($names | Where-Object { $reading.Active[$_] -ne 'True' })
    $state = (@($names | ForEach-Object { "$_ " + (Get-GboOnOff $reading.Active[$_]) }) -join ', ') + ' (in force)'
    if (@($reading.Products).Count -gt 0) { $state += '; registered firewall: ' + (@($reading.Products) -join ', ') }
    if (-not $reading.SecurityCenter) { $state += '; Security Center could not be read (it exists only on client editions of Windows)' }
    if ($off.Count -eq 0) { Add-GboRow 'firewall' 'ok' $state; return }
    $verdict = @(Get-GboFirewallVerdict $reading $off)
    Add-GboRow 'firewall' $verdict[0] ($state + $verdict[1])
}

# Get-GboProperty OBJECT NAME: the property's value, or $null when the object lacks
# it. StrictMode would throw on a missing property, and older Defender versions
# lack some of the ones read here.
function Get-GboProperty($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

# Get-GboDefenderReadings: Microsoft Defender's own status (or why it could not be
# asked), and the other antivirus products Security Center lists. Defender lists
# itself there too, so its own entries are left out.
function Get-GboDefenderReadings {
    $status = $null
    $problem = ''
    try { $status = Get-MpComputerStatus } catch { $problem = $_.Exception.Message }
    $products = @()
    $center = $true
    try {
        $products = @(Get-CimInstance -Namespace 'root/SecurityCenter2' -ClassName AntiVirusProduct |
            ForEach-Object { [string]$_.displayName } | Where-Object { $_ -notmatch 'Windows Defender|Microsoft Defender' })
    } catch { $center = $false }
    return [pscustomobject]@{ Status = $status; Problem = $problem; Products = $products; SecurityCenter = $center }
}

# Defender's out-of-date flag means something only while Defender is the antivirus
# in charge. When another antivirus is registered, Defender stops on purpose and its
# signatures go stale; reading that as 'at risk' would make a row that never turns
# green. Microsoft states no default age at which signatures count as out of date,
# so the row uses Defender's own flag rather than a number of days.
function Invoke-GboCheckDefender {
    $r = Get-GboDefenderReadings
    $others = @($r.Products)
    $registered = 'another antivirus is registered: ' + ($others -join ', ')
    if ($null -eq $r.Status) {
        if ($others.Count -gt 0) { Add-GboRow 'defender' 'info' "Microsoft Defender could not be asked ($($r.Problem)); $registered"; return }
        Add-GboRow 'defender' 'unknown' "could not ask Microsoft Defender: $($r.Problem)"
        return
    }
    $mode = [string](Get-GboProperty $r.Status 'AMRunningMode')
    if ((Get-GboProperty $r.Status 'RealTimeProtectionEnabled') -eq $true) { Add-GboDefenderOnRow $r.Status $mode; return }
    if ($others.Count -gt 0) { Add-GboRow 'defender' 'info' "Defender is off (running mode: $mode) because $registered"; return }
    if (-not $r.SecurityCenter) {
        Add-GboRow 'defender' 'unknown' "real-time protection is off (running mode: $mode); Security Center could not be read, so another antivirus cannot be seen"
        return
    }
    Add-GboRow 'defender' 'at risk' "real-time protection is off (running mode: $mode) and no other antivirus is registered"
}

function Add-GboDefenderOnRow($Status, [string]$Mode) {
    $age = Get-GboProperty $Status 'AntivirusSignatureAge'
    $stale = Get-GboProperty $Status 'DefenderSignaturesOutOfDate'
    if ($stale -eq $true) {
        Add-GboRow 'defender' 'at risk' "real-time protection on (running mode: $Mode), but its signatures are $age day(s) old and out of date by Defender's own flag"
        return
    }
    $flag = "not out of date by Defender's own flag"
    if ($null -eq $stale) { $flag = 'this Defender version has no out-of-date flag' }
    Add-GboRow 'defender' 'ok' "real-time protection on (running mode: $Mode); signatures $age day(s) old, $flag"
}

# Get-GboListenerReadings: every TCP socket listening on all interfaces (0.0.0.0 or
# ::), with its process name and logon session, and this script's own session.
# Every connection is listed and then filtered, because asking Windows for
# listening sockets alone raises an error when there are none.
function Get-GboListenerReadings {
    $mine = [Diagnostics.Process]::GetCurrentProcess().SessionId
    $listening = @(Get-NetTCPConnection | Where-Object {
        [string]$_.State -eq 'Listen' -and ($_.LocalAddress -eq '0.0.0.0' -or $_.LocalAddress -eq '::') })
    $sockets = foreach ($s in $listening) {
        $proc = Get-Process -Id $s.OwningProcess -ErrorAction SilentlyContinue
        $name = '?'
        $session = -1
        if ($proc) { $name = $proc.ProcessName; $session = $proc.SessionId }
        [pscustomobject]@{ Process = $name; Address = [string]$s.LocalAddress; Port = [int]$s.LocalPort; Session = $session }
    }
    return [pscustomobject]@{ MySession = $mine; Sockets = @($sockets) }
}

# bash: the listeners part of check_hygiene. lsof without sudo sees only the user's
# own sockets, so this row blames only processes in the user's own logon session.
# The services' sockets are counted, not blamed: asking for owners would need an
# elevated shell, and the session number is free (measured 2026-11-03: 32 ms).
function Invoke-GboCheckListeners {
    try { $r = Get-GboListenerReadings }
    catch { Add-GboRow 'listeners' 'unknown' ('could not list listening sockets: ' + $_.Exception.Message); return }
    $own = @($r.Sockets | Where-Object { $_.Session -eq $r.MySession })
    $others = @($r.Sockets).Count - $own.Count
    $names = @($own | ForEach-Object { '{0}:{1}:{2}' -f $_.Process, (Format-GboAddress $_.Address), $_.Port } | Sort-Object -Unique)
    $status = 'ok'
    $shown = 'none'
    if ($names.Count -gt 0) { $status = 'question'; $shown = $names -join ' ' }
    Add-GboRow 'listeners' $status "$($names.Count) listener(s) on all interfaces in your logon session: $shown; $others more belong to services or other sessions; bind dev servers to 127.0.0.1"
}

function Format-GboAddress([string]$Address) {
    if ($Address -eq '::') { return '[::]' }
    return $Address
}

# Get-GboSessionReadings: the interactive logon sessions that run processes, and how
# many processes run outside session 0, which belongs to the services.
function Get-GboSessionReadings {
    $ids = @(Get-Process | ForEach-Object { $_.SessionId } | Where-Object { $_ -gt 0 })
    return [pscustomobject]@{ Sessions = @($ids | Sort-Object -Unique); Processes = $ids.Count }
}

# bash: the accounts row. Naming a process's owner needs an elevated shell on
# Windows, so this counts interactive logon sessions instead, and names no one.
function Invoke-GboCheckAccounts {
    try { $r = Get-GboSessionReadings }
    catch { Add-GboRow 'accounts' 'unknown' ('could not list processes: ' + $_.Exception.Message); return }
    Add-GboRow 'accounts' 'info' "$(@($r.Sessions).Count) interactive logon session(s) run processes on this machine ($($r.Processes) processes outside the services' session 0; names withheld); Task Manager's Users tab names them"
}

# ---- Tailscale (bash: check_tailscale) ------------------------------------------------------
# The folders Windows installs programs into: ProgramW6432 names the 64-bit one
# even inside a 32-bit PowerShell, then this process's own, then the 32-bit one.
function Get-GboProgramRoots {
    $seen = @{}
    foreach ($root in @($env:ProgramW6432, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not (Test-GboFullPath $root)) { continue }
        $key = $root.TrimEnd('\').ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $root
    }
}

# Get-GboTailscale: the tailscale.exe Windows would run, or else the one in the
# Tailscale folder of a program root (bash looks in /Applications the same way).
# Only a .exe: the row passes it subcommands, never a single fixed flag.
function Get-GboTailscale {
    $found = @(Get-GboEachOnPath 'tailscale.exe')
    if ($found.Count -gt 0) { return $found[0] }
    foreach ($root in @(Get-GboProgramRoots)) {
        $exe = Join-Path $root 'Tailscale\tailscale.exe'
        if ([IO.File]::Exists($exe)) { return $exe }
    }
    return ''
}

# Get-GboTailscaleDaemon EXE: the version the running Tailscale service reports
# (tailscale version --daemon prints a "Daemon: 1.104.4-t..." line), or '' when
# the service does not answer.
function Get-GboTailscaleDaemon([string]$Exe) {
    $answer = Invoke-GboNative -Path $Exe -ArgumentList @('version', '--daemon') -TimeoutSeconds 15
    foreach ($line in ($answer.StdOut -split "`n")) {
        if ($line -match '^Daemon:\s*(\d+(?:\.\d+)+)') { return $Matches[1] }
    }
    return ''
}

# bash: check_tailscale. The service is what the network reaches, so its version
# decides; the command-line tool's version stands in only when the service does
# not answer.
function Invoke-GboCheckTailscale {
    $exe = Get-GboTailscale
    if (-not $exe) { Add-GboRow 'tailscale' 'n/a' 'no tailscale on PATH or in Program Files'; return }
    $cli = ''
    $first = Get-GboFirstLine (Invoke-GboNative -Path $exe -ArgumentList @('version') -TimeoutSeconds 15).StdOut
    if ($first -match '^(\d+(?:\.\d+)+)') { $cli = $Matches[1] }
    $daemon = Get-GboTailscaleDaemon $exe
    if ($daemon) {
        $evidence = "daemon $daemon; floor $FLOOR_TAILSCALE (TS-2026-013)"
        if ($cli -and $cli -ne $daemon) { $evidence += "; the command-line tool reports $cli and the running service ${daemon}: the service is what the network reaches" }
        Add-GboRow 'tailscale' (Get-GboStatusFor $daemon $FLOOR_TAILSCALE) $evidence
    } elseif ($cli) {
        Add-GboRow 'tailscale' (Get-GboStatusFor $cli $FLOOR_TAILSCALE) "command-line tool $cli (the Tailscale service did not answer); floor $FLOOR_TAILSCALE (TS-2026-013)"
    } else {
        Add-GboRow 'tailscale' 'unknown' "${exe}: answered neither tailscale version nor tailscale version --daemon"
    }
    Add-GboTailwormRow $exe
}

# bash: the tailworm half of check_tailscale. debug prefs prints every setting,
# node keys included, so only the RunSSH and Advertise (app connector) lines are
# kept, as bash keeps them. Serve entries that reach a unix socket or accept app
# capabilities are counted.
function Add-GboTailwormRow([string]$Exe) {
    $prefsOut = (Invoke-GboNative -Path $Exe -ArgumentList @('debug', 'prefs') -TimeoutSeconds 15).StdOut
    $kept = @($prefsOut -split "`n" | Where-Object { $_ -cmatch '"RunSSH"|"Advertise"' } | ForEach-Object { $_ -replace '[ \t]', '' })
    $prefs = $kept -join ' '
    $serveOut = (Invoke-GboNative -Path $Exe -ArgumentList @('serve', 'status', '--json') -TimeoutSeconds 15).StdOut
    $serve = @($serveOut -split "`n" | Where-Object { $_ -cmatch 'unix:|AcceptAppCaps' }).Count
    if (-not $prefs) { Add-GboRow 'tailworm' 'unknown' 'prefs unreadable (the Tailscale service is not running, or this account may not ask it)'; return }
    if ($prefs -cmatch '"RunSSH":true|"Advertise":true') {
        Add-GboRow 'tailworm' 'at risk' "$prefs serve-entries=$serve; a flagged feature is on (Tailscale SSH or the app connector)"
        return
    }
    Add-GboRow 'tailworm' 'ok' "$prefs serve-entries=$serve; none of the 11 Tailworm findings applies"
}

# ---- browsers (bash: check_chrome; the Edge row is new for Windows) ---------------------------
# Get-GboFileVersion PATH: the four version numbers Windows keeps in a program
# file's version block. Reading them needs no program to run, and the numbers do
# not depend on the language Windows is set to.
function Get-GboFileVersion([string]$Path) {
    $info = [Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
    return ('{0}.{1}.{2}.{3}' -f $info.FileMajorPart, $info.FileMinorPart, $info.FileBuildPart, $info.FilePrivatePart)
}

# Get-GboBrowserCopies RELATIVE: each installed copy of a browser: the machine-wide
# one in each program root, then the per-user one under LOCALAPPDATA.
function Get-GboBrowserCopies([string]$Relative) {
    $seen = @{}
    foreach ($root in @(@(Get-GboProgramRoots) + @($env:LOCALAPPDATA))) {
        $exe = Join-GboPath $root $Relative
        if (-not $exe -or -not [IO.File]::Exists($exe)) { continue }
        $key = $exe.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $exe
    }
}

function Add-GboBrowserRow([string]$Item, [string]$Name, [string]$Exe, [string]$Floor, [string]$Note) {
    $version = Get-GboFileVersion $Exe
    if ($version -eq '0.0.0.0') { Add-GboRow $Item 'unknown' "${Exe}: carries no version"; return }
    Add-GboRow $Item (Get-GboStatusFor $version $Floor) "$Name $version at $Exe; floor $Floor $Note"
}

# bash: check_chrome, for each installed copy.
function Invoke-GboCheckChrome {
    $copies = @(Get-GboBrowserCopies 'Google\Chrome\Application\chrome.exe')
    if ($copies.Count -eq 0) { Add-GboRow 'chrome' 'n/a' 'no Chrome found'; return }
    foreach ($exe in $copies) { Add-GboBrowserRow 'chrome' 'Chrome' $exe $FLOOR_CHROME '(353 security fixes)' }
}

# Edge comes with Windows and is built on Chromium, so it needs the same fixes.
function Invoke-GboCheckEdge {
    $copies = @(Get-GboBrowserCopies 'Microsoft\Edge\Application\msedge.exe')
    if ($copies.Count -eq 0) { Add-GboRow 'edge' 'n/a' 'no Edge found'; return }
    foreach ($exe in $copies) { Add-GboBrowserRow 'edge' 'Edge' $exe $FLOOR_EDGE "(the first Stable 156 with Chromium's fixes, 2026-10-22)" }
}

# ---- servers (bash: check_servers) ----------------------------------------------------------
# Get-GboMinor VERSION: the first two numbers, "8.4" for "8.4.13".
function Get-GboMinor([string]$Version) {
    return (@($Version.Split('.') | Select-Object -First 2) -join '.')
}

# bash: the php part of check_servers, for each php.exe on PATH. php -v prints
# "PHP 8.4.13 (cli) ..."; the second word is the version.
function Invoke-GboCheckPhp {
    $phps = @(Get-GboEachOnPath 'php.exe')
    if ($phps.Count -eq 0) { Add-GboRow 'php' 'n/a' 'no php'; return }
    $floors = @{ '8.5' = $FLOOR_PHP_85; '8.4' = $FLOOR_PHP_84; '8.3' = $FLOOR_PHP_83; '8.2' = $FLOOR_PHP_82 }
    foreach ($php in $phps) {
        $words = @((Get-GboFirstLine (Invoke-GboNative -Path $php -ArgumentList @('-v') -TimeoutSeconds 15).StdOut) -split '\s+')
        $version = ''
        if ($words.Count -gt 1) { $version = $words[1] }
        if ($version -notmatch '^\d+\.\d+') { Add-GboRow 'php' 'unknown' "${php}: does not answer -v"; continue }
        $floor = $floors[(Get-GboMinor $version)]
        if (-not $floor) { Add-GboRow 'php' 'unknown' "php $version at $php; no fixed release for this branch"; continue }
        Add-GboRow 'php' (Get-GboStatusFor $version $floor) "php $version at $php; floor $floor (PHP2Shell)"
    }
}

# bash: the gitlab part of check_servers.
function Invoke-GboCheckGitlab {
    if (@(Get-GboEachOnPath 'gitlab-ctl').Count -gt 0) {
        Add-GboRow 'gitlab' 'at risk' 'gitlab-ctl present: every self-managed release since 7.0 is affected (CVE-2026-110264); update'
        return
    }
    Add-GboRow 'gitlab' 'n/a' 'no gitlab-ctl'
}

# bash: the ruby part of check_servers, for each ruby.exe on PATH; with none there
# is no row, as in bash. ruby -v prints "ruby 3.4.7 (...)"; a patch level such as
# "3.2.2p53" is cut at the p, as bash's sed 's/p.*//' cuts it.
function Invoke-GboCheckRuby {
    $floors = @{ '4.0' = $FLOOR_RUBY_40; '3.4' = $FLOOR_RUBY_34; '3.3' = $FLOOR_RUBY_33 }
    foreach ($ruby in @(Get-GboEachOnPath 'ruby.exe')) {
        $words = @((Get-GboFirstLine (Invoke-GboNative -Path $ruby -ArgumentList @('-v') -TimeoutSeconds 15).StdOut) -split '\s+')
        $version = ''
        if ($words.Count -gt 1) { $version = $words[1] -creplace 'p.*$', '' }
        if ($version -notmatch '^\d+\.\d+') { Add-GboRow 'ruby' 'unknown' "${ruby}: does not answer -v"; continue }
        $floor = $floors[(Get-GboMinor $version)]
        if (-not $floor) { Add-GboRow 'ruby' 'unknown' "$ruby $version; no fix on this branch (3.2 and older get none)"; continue }
        Add-GboRow 'ruby' (Get-GboStatusFor $version $floor) "$ruby $version; floor $floor (regexp fixes)"
    }
}

# Get-GboPackageFiles: each node_modules\next and node_modules\storybook package
# file within six levels of a code folder (bash: find -maxdepth 6 -path
# '*/node_modules/next/package.json'). Such a file sits three levels below the
# folder that holds node_modules, so the walk visits folders up to three levels
# deep, node_modules included, and asks each one. Like find, it does not follow a
# junction while walking.
function Get-GboPackageFiles {
    foreach ($dir in @(Get-GboCodeDirs)) {
        $queue = New-Object 'System.Collections.Generic.Queue[object]'
        $queue.Enqueue(@((New-Object IO.DirectoryInfo($dir)), 0))
        while ($queue.Count -gt 0) {
            $folder, $depth = $queue.Dequeue()
            foreach ($name in @('next', 'storybook')) {
                $file = Join-Path $folder.FullName "node_modules\$name\package.json"
                if ([IO.File]::Exists($file)) { [pscustomobject]@{ Name = $name; File = $file; Project = $folder.FullName } }
            }
            if ($depth -ge 3) { continue }
            try { $subs = $folder.GetDirectories() } catch { continue }
            foreach ($sub in $subs) { if (-not (Test-GboLinkFolder $sub)) { $queue.Enqueue(@($sub, ($depth + 1))) } }
        }
    }
}

# The first "version" value in a package.json (bash: grep -m1 '"version"').
function Get-GboPackageVersion([string]$File) {
    if ([IO.File]::ReadAllText($File) -match '"version"\s*:\s*"([^"]*)"') { return $Matches[1] }
    return ''
}

# bash: the nextjs and storybook parts of check_servers. One row per project; a
# project reached through two code folders (~\.hal is often a junction into
# ~\CodeTonight) counts once, by its real path. Two projects that share one
# installed copy, as pnpm arranges with junctions, each keep their row.
function Invoke-GboCheckPackages {
    $seen = @{}
    foreach ($found in @(Get-GboPackageFiles)) {
        $key = $found.Name + '|' + (Resolve-GboLinkedPath (Join-Path $found.Project 'node_modules')).ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        Add-GboPackageRow $found (Get-GboPackageVersion $found.File)
    }
}

function Add-GboPackageRow($Found, [string]$Version) {
    $major = $Version.Split('.')[0]
    $item = 'nextjs'
    $note = "(and sharp $FLOOR_SHARP); matters when self-hosted"
    $floors = @{ '16' = $FLOOR_NEXT_16; '15' = $FLOOR_NEXT_15 }
    if ($Found.Name -eq 'storybook') {
        $item = 'storybook'
        $note = '(CVE-2026-114125, dev server only)'
        $floors = @{ '8' = $FLOOR_STORYBOOK_8; '9' = $FLOOR_STORYBOOK_9; '10' = $FLOOR_STORYBOOK_10 }
    }
    $where = "$($Found.Name) $Version in $($Found.Project)"
    if (-not $floors.ContainsKey($major)) { Add-GboRow $item 'unknown' "$where; no fixed line for major $major"; return }
    Add-GboRow $item (Get-GboStatusFor $Version $floors[$major]) "$where; floor $($floors[$major]) $note"
}

# bash: the pillow part of check_servers, for each Python that answers. Pillow's
# version is read from its installed metadata, so none of Pillow's own code runs,
# and olefile is looked up as bash looks it up, without loading it. Below the
# floor, the FPX decoder is reachable only with olefile installed.
function Invoke-GboCheckPillow {
    $versionCode = 'import importlib.util as u, importlib.metadata as m; print(m.version("pillow") if u.find_spec("PIL") else "")'
    $oleCode = 'import importlib.util as u; print("yes" if u.find_spec("olefile") else "no")'
    foreach ($python in @(Get-GboPythonCandidates)) {
        $version = Get-GboFirstLine (Invoke-GboNative -Path $python -ArgumentList @('-c', $versionCode)).StdOut
        if ($version -notmatch '^\d+(\.\d+)*$') { continue }
        $ole = Get-GboFirstLine (Invoke-GboNative -Path $python -ArgumentList @('-c', $oleCode)).StdOut
        $status = 'ok'
        if (-not (Test-GboVge $version $FLOOR_PILLOW) -and $ole -eq 'yes') { $status = 'at risk' }
        Add-GboRow 'pillow' $status "$python Pillow $version olefile=$ole; floor $FLOOR_PILLOW; the FPX decoder is reachable only with olefile installed"
    }
}

# ---- keys and tokens (bash: check_keys_platforms, the npm and .env parts of check_hygiene) --
# bash: check_keys_platforms. Always a question, because only the operator knows
# whether a key was stored on one of these platforms. The evidence names the
# tools and folders that are present; neither tool is run.
function Invoke-GboCheckKeysPlatforms {
    $present = @()
    if (@(Get-GboEachOnPath 'railway').Count -gt 0) { $present += 'railway-cli' }
    if (@(Get-GboEachOnPath 'opencode').Count -gt 0) { $present += 'opencode-cli' }
    foreach ($folder in @('.railway', '.config\opencode')) {
        $full = Join-GboPath $env:USERPROFILE $folder
        if ($full -and [IO.Directory]::Exists($full)) { $present += "~\$folder" }
    }
    $shown = 'none'
    if ($present.Count -gt 0) { $shown = $present -join ' ' }
    Add-GboRow 'keys-platforms' 'question' "present: $shown; the operator must say whether any key was stored on OpenCode Console, Treg, Railway, Fireworks AI or TiDB Cloud (rotate if so)"
}

# bash: the npm part of check_hygiene. On Windows npm keeps its user settings in
# %USERPROFILE%\.npmrc. Lines are counted; a value is never shown.
function Invoke-GboCheckNpmTokens {
    $rc = Join-GboPath $env:USERPROFILE '.npmrc'
    $count = 0
    if ($rc -and [IO.File]::Exists($rc)) {
        try { $count = @([IO.File]::ReadAllLines($rc) | Where-Object { $_.Contains('_authToken') }).Count }
        catch { Add-GboRow 'npm-tokens' 'unknown' ('could not read ~\.npmrc: ' + $_.Exception.Message); return }
    }
    $status = 'ok'
    if ($count -gt 0) { $status = 'question' }
    Add-GboRow 'npm-tokens' $status "$count _authToken line(s) in ~\.npmrc (values never shown); check each against its registry before calling it dead"
}

# The groups that stand for "other accounts" on Windows, as the o=r bit does in
# bash: Everyone, Interactive (whoever is signed in at the keyboard), Authenticated
# Users and the local Users group. Every other account that can sign in to this
# PC belongs to them.
$GboBroadSids = @('S-1-1-0', 'S-1-5-4', 'S-1-5-11', 'S-1-5-32-545')

# The folders the .env check searches (bash: code_dirs): GBO_CODE_DIRS, full
# paths separated by ';' because a Windows path holds ':', or else bash's six
# folders under the user's profile and the two places Windows tools put code by
# default: source\repos (Visual Studio) and Documents\GitHub (GitHub Desktop).
# Only folders that exist count, each once.
function Get-GboCodeDirs {
    $dirs = @('CodeTonight', '.grip', '.hal', 'Developer', 'code', 'src', 'source\repos', 'Documents\GitHub') |
        ForEach-Object { Join-GboPath $env:USERPROFILE $_ }
    if ($env:GBO_CODE_DIRS) { $dirs = @($env:GBO_CODE_DIRS -split ';') }
    $seen = @{}
    foreach ($dir in $dirs) {
        if (-not (Test-GboFullPath $dir) -or -not [IO.Directory]::Exists($dir)) { continue }
        $full = [IO.Path]::GetFullPath($dir)
        $key = $full.TrimEnd('\').ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $full
    }
}

# Get-GboEnvFiles DIR: the .env* files at most four levels below DIR (bash: find
# -maxdepth 4 -name '.env*'), except *.example, *.sample and *.template, and the
# folders that could not be listed. Like find, it does not follow a junction or
# symbolic link inside DIR, and it does not enter node_modules, where bash leaves
# out every match.
function Get-GboEnvFiles([string]$Dir) {
    $files = New-Object 'System.Collections.Generic.List[string]'
    $unlisted = New-Object 'System.Collections.Generic.List[string]'
    $queue = New-Object 'System.Collections.Generic.Queue[object]'
    $queue.Enqueue(@((New-Object IO.DirectoryInfo($Dir)), 0))
    while ($queue.Count -gt 0) {
        $folder, $depth = $queue.Dequeue()
        try {
            foreach ($file in $folder.GetFiles('.env*')) {
                if (Test-GboEnvName $file.Name) { $files.Add($file.FullName) }
            }
            if ($depth -ge 3) { continue }
            foreach ($sub in $folder.GetDirectories()) {
                if ($sub.Name -eq 'node_modules' -or (Test-GboLinkFolder $sub)) { continue }
                $queue.Enqueue(@($sub, ($depth + 1)))
            }
        } catch { $unlisted.Add($folder.FullName) }
    }
    return [pscustomobject]@{ Files = $files.ToArray(); Unlisted = $unlisted.ToArray() }
}

# The search pattern '.env*' can also match a file's old 8.3 short name, so the
# long name is checked again here.
function Test-GboEnvName([string]$Name) {
    if (-not $Name.StartsWith('.env', [StringComparison]::OrdinalIgnoreCase)) { return $false }
    foreach ($suffix in @('.example', '.sample', '.template')) {
        if ($Name.EndsWith($suffix, [StringComparison]::OrdinalIgnoreCase)) { return $false }
    }
    return $true
}

# A junction or symbolic link is not followed. Any other reparse point, such as
# a OneDrive folder, is searched like an ordinary folder.
function Test-GboLinkFolder([IO.DirectoryInfo]$Folder) {
    if (($Folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) { return $false }
    $item = Get-Item -LiteralPath $Folder.FullName -Force -ErrorAction SilentlyContinue
    return ($null -ne $item -and [bool]$item.LinkType)
}

# Get-GboAceMask RULE: the rule's access mask as an unsigned number. The generic
# rights sit in the top bits, which a signed 32-bit number shows as negative.
function Get-GboAceMask($Rule) {
    $mask = [int64][int]$Rule.FileSystemRights
    if ($mask -lt 0) { $mask += 4294967296 }
    return $mask
}

# Reading a file's contents needs ReadData (1). GENERIC_READ (0x80000000) and
# GENERIC_ALL (0x10000000) include it: 1 + 2147483648 + 268435456 = 2415919105.
function Test-GboReadMask([int64]$Mask) {
    return (($Mask -band 2415919105) -ne 0)
}

# Test-GboBroadRead RULES: whether access rules, taken in the order Windows reads
# them, let the broad groups read a file's contents. Windows stops at the first
# rule that names one of the groups and covers reading: a deny rule there stops
# the read, and an allow rule grants it. No such rule means no access.
function Test-GboBroadRead($Rules) {
    foreach ($rule in @($Rules)) {
        if ($GboBroadSids -notcontains [string]$rule.IdentityReference) { continue }
        if (($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0) { continue }
        if (-not (Test-GboReadMask (Get-GboAceMask $rule))) { continue }
        return ([string]$rule.AccessControlType -eq 'Allow')
    }
    return $false
}

# Test-GboEnvFileOpen PATH: $true when other accounts can read the file, $false
# when they cannot, and $null when its access list could not be read. Only the
# access list is read, never the file itself.
function Test-GboEnvFileOpen([string]$Path) {
    try { $acl = Get-Acl -LiteralPath $Path }
    catch { return $null }
    return (Test-GboBroadRead ($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])))
}

# bash: the .env part of check_hygiene. A file other accounts can read is at risk;
# a file or folder that could not be checked makes the row unknown, never ok.
# A code folder that is itself a junction is searched, because it was named on
# purpose (find would skip it). Each file then counts once, by its real path,
# because two code folders can reach one tree: ~\.hal is often a junction into
# ~\CodeTonight.
function Invoke-GboCheckEnvPerms {
    $dirs = @(Get-GboCodeDirs)
    if ($dirs.Count -eq 0) {
        Add-GboRow 'env-perms' 'n/a' 'none of the code folders exists (GBO_CODE_DIRS names others: full paths separated by ;)'
        return
    }
    $total = 0
    $open = 0
    $unread = 0
    $unlisted = @()
    $seen = @{}
    foreach ($dir in $dirs) {
        $walk = Get-GboEnvFiles $dir
        $unlisted += @($walk.Unlisted)
        foreach ($file in @($walk.Files)) {
            $key = (Resolve-GboLinkedPath $file).ToLowerInvariant()
            if ($seen.ContainsKey($key)) { continue }
            $seen[$key] = $true
            $total++
            $verdict = Test-GboEnvFileOpen $file
            if ($null -eq $verdict) { $unread++ } elseif ($verdict) { $open++ }
        }
    }
    $evidence = "$open of $total .env* file(s) can be read by other accounts (Everyone, Interactive, Authenticated Users or Users) under " + ($dirs -join ', ') + '; example, sample and template files excluded'
    $status = 'ok'
    if ($unread -gt 0) { $status = 'unknown'; $evidence += "; $unread access list(s) could not be read" }
    if ($unlisted.Count -gt 0) { $status = 'unknown'; $evidence += "; $($unlisted.Count) folder(s) could not be listed, the first $($unlisted[0])" }
    if ($open -gt 0) { $status = 'at risk' }
    Add-GboRow 'env-perms' $status $evidence
}

# ---- what a program file carries (bash: strings, brotli_fixed) ----------------------------
# Get-GboFileClues PATH: read a program file as bytes, as bash's strings | grep
# does. Control and Fix count the README's brotli marker words: control > 0 with
# fix = 0 reads as an unpatched decoder, and control = 0 means the marker does not
# apply (a clue, not proof). Bun is the first "Bun v1.x.y" version in the file, or
# ''. The file is read in 16 MB pieces, each repeating the last 64 characters of
# the one before, so a word cut in two at a boundary is still found.
function Get-GboFileClues([string]$Path) {
    $latin1 = [Text.Encoding]::GetEncoding(28591)   # one character per byte
    $control = 0
    $fix = 0
    $bun = ''
    $carry = ''
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
    try {
        $buffer = New-Object byte[] 16777216
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $text = $carry + $latin1.GetString($buffer, 0, $read)
            $control += Measure-GboWord $text 'FORMAT_EXUBERANT_NIBBLE' $carry.Length
            $fix += Measure-GboWord $text 'FORMAT_BLOCK_SWITCH' $carry.Length
            if (-not $bun) { $bun = Find-GboBunVersion $text ($stream.Position -ge $stream.Length) }
            $carry = $text.Substring([Math]::Max(0, $text.Length - 64))
        }
    } finally { $stream.Dispose() }
    return [pscustomobject]@{ Control = $control; Fix = $fix; Bun = $bun }
}

# Measure-GboWord TEXT WORD SKIP: how many times WORD occurs in TEXT. An occurrence
# that ends within the first SKIP characters, the part repeated from the piece
# before, was counted there and is left out here.
function Measure-GboWord([string]$Text, [string]$Word, [int]$Skip) {
    $count = 0
    $at = $Text.IndexOf($Word, [StringComparison]::Ordinal)
    while ($at -ge 0) {
        if ($at + $Word.Length -gt $Skip) { $count++ }
        $at = $Text.IndexOf($Word, $at + 1, [StringComparison]::Ordinal)
    }
    return $count
}

# Find-GboBunVersion TEXT LAST: the first "Bun v1.x.y" in TEXT whose version can be
# read whole (bash: grep -m1 -oE 'Bun v1\.[0-9]+\.[0-9]+'). A mention too near the
# end of a piece waits for the next piece, which repeats it, unless this piece is
# the last: cut at "Bun v1.10.2", a "1.10.22" would read as 1.10.2.
function Find-GboBunVersion([string]$Text, [bool]$Last) {
    $at = $Text.IndexOf('Bun v1.', [StringComparison]::Ordinal)
    while ($at -ge 0) {
        if (-not $Last -and $at + 32 -gt $Text.Length) { return '' }
        $tail = $Text.Substring($at, [Math]::Min(32, $Text.Length - $at))
        if ($tail -cmatch '^Bun v(1\.\d+\.\d+)') { return $Matches[1] }
        $at = $Text.IndexOf('Bun v1.', $at + 1, [StringComparison]::Ordinal)
    }
    return ''
}

function Format-GboMarker($Clues) {
    return "control=$($Clues.Control) fix=$($Clues.Fix)"
}

# Resolve-GboProgram PATH: the program file behind PATH (bash: readlink -f). npm
# installs a command as a .cmd shim whose last "%dp0%\node_modules\..." line runs
# the real file; that file is returned when it exists. Any other file is its own.
function Resolve-GboProgram([string]$Path) {
    if ([IO.Path]::GetExtension($Path) -ne '.cmd') { return $Path }
    $target = ''
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match '"%dp0%\\(node_modules\\[^"]+)"') { $target = $Matches[1] }
    }
    if (-not $target) { return $Path }
    $full = Join-Path (Split-Path -Parent $Path) $target
    if ([IO.File]::Exists($full)) { return $full }
    return $Path
}

# Get-GboVersionLine FOUND PROGRAM: the first line a program prints for --version.
# PROGRAM, the file behind FOUND, is asked when it is a .exe; otherwise the shim
# FOUND is asked, with nothing but that fixed flag.
function Get-GboVersionLine([string]$Found, [string]$Program) {
    $ask = $Found
    if ([IO.Path]::GetExtension($Program) -eq '.exe') { $ask = $Program }
    return (Get-GboFirstLine (Invoke-GboNative -Path $ask -ArgumentList @('--version') -TimeoutSeconds 15).StdOut)
}

# ---- programs built on Bun (bash: check_bun, check_claude) --------------------------------
# bash: check_bun, for each bun and each opencode on PATH. opencode embeds its own
# Bun, which only a new opencode release can replace, so its row reports only.
function Invoke-GboCheckBun {
    $buns = @(Get-GboEachOnPath 'bun')
    if ($buns.Count -eq 0) { Add-GboRow 'bun' 'n/a' 'no standalone bun on PATH' }
    foreach ($found in $buns) {
        $version = Get-GboVersionLine $found (Resolve-GboProgram $found)
        if ($version -notmatch '^\d+\.\d+') { Add-GboRow 'bun' 'unknown' "${found}: does not answer --version"; continue }
        Add-GboRow 'bun' (Get-GboStatusFor $version $FLOOR_BUN) "bun $version at $found; floor $FLOOR_BUN"
    }
    foreach ($found in @(Get-GboEachOnPath 'opencode')) {
        $program = Resolve-GboProgram $found
        $version = Get-GboVersionLine $found $program
        $clues = Get-GboFileClues $program
        $bun = $clues.Bun
        if (-not $bun) { $bun = '?' }
        Add-GboRow 'bun-tools' (Get-GboStatusFor $clues.Bun $FLOOR_BUN) "opencode $version at $program embeds Bun $bun; floor $FLOOR_BUN; marker $(Format-GboMarker $clues); report only: wait for an opencode release with a fixed Bun"
    }
}

# bash: check_claude, for each claude on PATH. --version prints "2.1.317 (Claude
# Code)"; the first word is the version.
function Invoke-GboCheckClaude {
    $claudes = @(Get-GboEachOnPath 'claude')
    if ($claudes.Count -eq 0) { Add-GboRow 'claude' 'n/a' 'no claude on PATH'; return }
    $settings = Join-GboPath $env:USERPROFILE '.claude\settings.json'
    $deny = 0
    if ($settings -and [IO.File]::Exists($settings)) {
        $deny = @([IO.File]::ReadAllLines($settings) | Where-Object { $_.Contains('"WebFetch"') }).Count
    }
    foreach ($found in $claudes) {
        $program = Resolve-GboProgram $found
        $version = @((Get-GboVersionLine $found $program) -split '\s+')[0]
        $clues = Get-GboFileClues $program
        $bun = 'no Bun string'
        if ($clues.Bun) { $bun = "Bun v$($clues.Bun)" }
        Add-GboRow 'claude' (Get-GboStatusFor $version $FLOOR_CLAUDE) "Claude Code $version at $program ($bun); floor $FLOOR_CLAUDE; marker $(Format-GboMarker $clues); WebFetch deny lines in settings: $deny"
    }
}

# ---- brotli (bash: check_brotli) ------------------------------------------------------------
# Each Git for Windows build keeps its own programs in one folder, and its
# etc\package-versions.txt names their packages with that build's prefix. Read from
# the MinGit zips of v2.56.0.windows.4 on 2026-11-05, each zip's SHA-256 as its
# release notes list it: ucrt64, clangarm64 and mingw32. The 64-bit build moved from
# mingw64 to ucrt64 in v2.56.0 ("/mingw64/bin/git.exe does not exist anymore,
# /ucrt64/bin/git.exe takes its role"), and A>>'s Git 2.50.1 has mingw64.
$GboGitBuilds = [ordered]@{
    'mingw64' = 'mingw-w64-x86_64'
    'ucrt64' = 'mingw-w64-ucrt-x86_64'
    'clangarm64' = 'mingw-w64-clang-aarch64'
    'mingw32' = 'mingw-w64-i686'
}

# Get-GboBrotliLibraries: the shared brotli decoders on this PC, the counterpart of
# the system libbrotlidec bash's Linux branch reads. Windows ships none. Git for
# Windows ships one for its own curl, in the bin and libexec\git-core folders of its
# build's folder, in the Git folder above each git.exe on PATH, or of Git in a
# program root. Any libbrotlidec*.dll in a PATH folder counts too.
function Get-GboBrotliLibraries {
    $gitRoots = @(@(Get-GboEachOnPath 'git.exe') | ForEach-Object { Split-Path -Parent (Split-Path -Parent $_) })
    $gitRoots += @(@(Get-GboProgramRoots) | ForEach-Object { Join-Path $_ 'Git' })
    $relatives = @($GboGitBuilds.Keys | ForEach-Object { "$_\bin\libbrotlidec.dll"; "$_\libexec\git-core\libbrotlidec.dll" })
    $found = @()
    foreach ($root in $gitRoots) {
        foreach ($relative in $relatives) {
            $dll = Join-Path $root $relative
            if ([IO.File]::Exists($dll)) { $found += $dll }
        }
    }
    foreach ($entry in ($env:Path -split ';')) {
        $dir = [Environment]::ExpandEnvironmentVariables($entry.Trim().Trim('"'))
        if (-not (Test-GboFullPath $dir) -or -not [IO.Directory]::Exists($dir)) { continue }
        $found += @([IO.Directory]::GetFiles($dir, 'libbrotlidec*.dll') | Where-Object { [IO.Path]::GetExtension($_) -eq '.dll' })
    }
    $seen = @{}
    foreach ($dll in $found) {
        $full = [IO.Path]::GetFullPath($dll)
        if ($seen.ContainsKey($full.ToLowerInvariant())) { continue }
        $seen[$full.ToLowerInvariant()] = $true
        $full
    }
}

# Get-GboGitBrotliVersion DLL: the brotli version Git for Windows records for the
# copy it ships, in etc\package-versions.txt under the prefix of the build whose
# folder holds the DLL ("mingw-w64-ucrt-x86_64-brotli 1.2.1-1", where -1 is the
# package's own release number), or '' for any other file.
function Get-GboGitBrotliVersion([string]$Dll) {
    $builds = @($GboGitBuilds.Keys | ForEach-Object { [regex]::Escape($_) }) -join '|'
    if ($Dll -notmatch ('^(.+)\\({0})\\(?:bin|libexec\\git-core)\\libbrotlidec\.dll$' -f $builds)) { return '' }
    $list = Join-Path $Matches[1] 'etc\package-versions.txt'
    $prefix = [regex]::Escape($GboGitBuilds[$Matches[2].ToLowerInvariant()])
    if (-not [IO.File]::Exists($list)) { return '' }
    foreach ($line in [IO.File]::ReadAllLines($list)) {
        if ($line -match ('^{0}-brotli (\S+?)(?:-\d+)?$' -f $prefix)) { return $Matches[1] }
    }
    return ''
}

# bash: check_brotli, for each shared decoder found. A copy Git for Windows ships is
# judged by the brotli version Git records for it, as bash's macOS branch judges
# Homebrew's. Any other copy falls back to bash's Linux branch, the README's marker,
# a clue, not proof: fix = 0 with control > 0 reads as unpatched, and control = 0
# means the marker does not apply to the file.
function Invoke-GboCheckBrotli {
    $libs = @(Get-GboBrotliLibraries)
    if ($libs.Count -eq 0) { Add-GboRow 'brotli' 'n/a' 'no shared brotli decoder found (in Git for Windows or on PATH)' }
    foreach ($lib in $libs) {
        $clues = Get-GboFileClues $lib
        $version = Get-GboGitBrotliVersion $lib
        if ($version) {
            Add-GboRow 'brotli' (Get-GboStatusFor $version $FLOOR_BROTLI) "$lib from Git for Windows: brotli $version (its etc\package-versions.txt); floor $FLOOR_BROTLI; marker $(Format-GboMarker $clues)"
            continue
        }
        $marker = "$lib marker $(Format-GboMarker $clues)"
        if ($clues.Control -eq 0) { Add-GboRow 'brotli' 'unknown' "$marker (the marker does not apply to this file)"; continue }
        if ($clues.Fix -eq 0) {
            Add-GboRow 'brotli' 'at risk' "$marker (unpatched by the marker; update the program that ships it to one with brotli $FLOOR_BROTLI or later)"
            continue
        }
        Add-GboRow 'brotli' 'unknown' "$marker (patched by the marker; confirm with the release notes of the program that ships it)"
    }
    Add-GboBrotliInMemoryRow
}

# Get-GboNodeProcesses: the program file of each running node process this account
# may read; another account's process cannot be read and is left out.
function Get-GboNodeProcesses {
    foreach ($proc in @(Get-Process -Name 'node' -ErrorAction SilentlyContinue)) {
        $path = ''
        try { $path = $proc.MainModule.FileName } catch { $path = '' }
        if ($path) { $path }
    }
}

# bash: brotli-in-memory, which on macOS finds processes holding an old brotli
# library through lsof. Node.js carries its own brotli, so here the row counts this
# account's running node processes whose program is below its floor. An installer
# cannot overwrite a running program's file, so until they restart, its version is
# normally the version they run.
function Add-GboBrotliInMemoryRow {
    $old = @()
    foreach ($path in @(Get-GboNodeProcesses)) {
        $version = @((Get-GboFileVersion $path).Split('.') | Select-Object -First 3) -join '.'
        $floor = Get-GboNodeFloor $version.Split('.')[0]
        if (-not $floor -or -not (Test-GboVge $version $floor)) { $old += "node $version" }
    }
    $summary = 'none'
    $status = 'ok'
    if ($old.Count -gt 0) {
        $summary = @($old | Group-Object | Sort-Object Name | ForEach-Object { "$($_.Name) x$($_.Count)" }) -join ', '
        $status = 'at risk'
    }
    Add-GboRow 'brotli-in-memory' $status "$($old.Count) node process(es) of this account hold a brotli older than the fix in memory ($summary); Node.js carries its own brotli, so restart them once Node is updated; other accounts' processes are not seen"
}

# ---- the incident's certificates ----------------------------------------------------------
# ConvertFrom-GboPem PEM: the certificate a PEM block holds.
function ConvertFrom-GboPem([string]$Pem) {
    $body = ($Pem -replace '-----[^-]+-----', '') -replace '\s', ''
    return (New-Object Security.Cryptography.X509Certificates.X509Certificate2(, [Convert]::FromBase64String($body)))
}

# The work folder (bash: WORK_DIR): GBO_WORK_DIR, or .grip-black-october in the
# user's profile. check reads it and never creates it.
function Get-GboWorkDir {
    if ($env:GBO_WORK_DIR) { return $env:GBO_WORK_DIR }
    return (Join-GboPath $env:USERPROFILE '.grip-black-october')
}

function Get-GboSha256([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return (-join ($sha.ComputeHash($Bytes) | ForEach-Object { $_.ToString('x2') })) } finally { $sha.Dispose() }
}

# Read-GboPinnedList NAME PIN: the entries of the incident page's NAME for this run:
# the list check -Network already fetched, or else the reviewed copy in the work
# folder. The bytes are read once, hashed, and parsed from those same bytes, so
# nothing can change between the check and the use. A missing copy, or one whose
# sha256 is not PIN, gives a reason instead of entries.
function Read-GboPinnedList([string]$Name, [string]$Pin) {
    if ($GboPinnedCache.ContainsKey($Name)) { return $GboPinnedCache[$Name] }
    $path = Join-Path (Get-GboWorkDir) $Name
    if (-not [IO.File]::Exists($path)) {
        return [pscustomobject]@{ Entries = @(); Problem = "no reviewed copy of $Name at ${path}: check -Network fetches it for its own run, or save $INCIDENT_BASE/$Name there (its sha256 must be $Pin)" }
    }
    return (ConvertFrom-GboPinnedBytes ([IO.File]::ReadAllBytes($path)) $Pin $path)
}

# Get-GboStoreThumbs NAME LOCATION: the SHA-1 thumbprints in one certificate store,
# opened read-only and never created. CurrentUser's Root and CA views include the
# machine's certificates, and they are the two stores Python's ssl reads (measured
# 2026-11-03: ssl.enum_certificates gave 91 ROOT and 54 CA, as these views do).
function Get-GboStoreThumbs([string]$Name, [string]$Location) {
    $store = New-Object Security.Cryptography.X509Certificates.X509Store($Name, $Location)
    $store.Open([Security.Cryptography.X509Certificates.OpenFlags]'ReadOnly, OpenExistingOnly')
    try { return @($store.Certificates | ForEach-Object { $_.Thumbprint }) } finally { $store.Close() }
}

# Get-GboAutoList: Microsoft's automatic list of distrusted certificates, which
# Windows keeps in the registry and refreshes by itself, as one character per byte,
# and when Windows last refreshed it. Problem says why it could not be read.
function Get-GboAutoList { return (Get-GboAutoUpdateList 'DisallowedCertEncodedCtl' 'DisallowedCertLastSyncTime') }

# Get-GboAutoUpdateList VALUE SYNC: one of the lists Windows refreshes by itself from
# Microsoft, as kept in the registry (one character per byte), and when it last did.
function Get-GboAutoUpdateList([string]$Value, [string]$Sync) {
    $none = [pscustomobject]@{ Text = ''; Synced = ''; Problem = '' }
    try {
        $item = Get-Item -LiteralPath 'HKLM:\SOFTWARE\Microsoft\SystemCertificates\AuthRoot\AutoUpdate'
        $list = $item.GetValue($Value)
        $stamp = $item.GetValue($Sync)
    } catch { $none.Problem = $_.Exception.Message; return $none }
    if ($null -eq $list) { $none.Problem = "$Value is missing"; return $none }
    $synced = 'at an unrecorded time'
    if ($stamp -is [byte[]] -and $stamp.Length -eq 8) {
        $synced = [DateTime]::FromFileTimeUtc([BitConverter]::ToInt64($stamp, 0)).ToString('yyyy-MM-dd HH:mm', [Globalization.CultureInfo]::InvariantCulture) + 'Z'
    }
    return [pscustomobject]@{ Text = [Text.Encoding]::GetEncoding(28591).GetString([byte[]]$list); Synced = $synced; Problem = '' }
}

# Get-GboTrustedList: Microsoft's automatic list of trusted roots, which Windows uses
# to add a root on demand.
function Get-GboTrustedList { return (Get-GboAutoUpdateList 'EncodedCtl' 'LastSyncTime') }

# Get-GboDigestNeedle HEX: how Microsoft's trusted list names a certificate by its
# SHA-256: a 32-byte OCTET STRING, the bytes 04 20 and then the digest. Measured
# 2026-11-05: all 71 roots in this PC's AuthRoot store are found this way.
function Get-GboDigestNeedle([string]$Hex) {
    $bytes = New-Object byte[] 34
    $bytes[0] = 4
    $bytes[1] = 32
    for ($i = 0; $i -lt 32; $i++) { $bytes[$i + 2] = [Convert]::ToByte($Hex.Substring(2 * $i, 2), 16) }
    return [Text.Encoding]::GetEncoding(28591).GetString($bytes)
}

# Get-GboKeyNeedle CERT: how the automatic list names a certificate: the MD5 of its
# public key, as a 16-byte OCTET STRING (the bytes 04 10, then the hash). Measured
# 2026-11-03: all 16 incident intermediates appear this way, and none by thumbprint.
function Get-GboKeyNeedle($Cert) {
    $md5 = [Security.Cryptography.MD5]::Create()
    try { $hash = $md5.ComputeHash($Cert.PublicKey.EncodedKeyValue.RawData) } finally { $md5.Dispose() }
    return [Text.Encoding]::GetEncoding(28591).GetString([byte[]](@(4, 16) + @($hash)))
}

# cert-store (no bash counterpart: bash edits Apple's cert.pem; Windows keeps stores).
# Where the incident's roots and intermediates sit in the two stores Python's ssl
# reads, which it trusts without Windows' block list. An incident root in the Root
# store, or an incident intermediate in the CA store (Python 3.13 and newer treat one
# as a trust anchor), is at risk. With neither there, the row is ok while Microsoft's
# trusted list names none of the incident roots, and a question while it still names
# one, because Windows adds a root from that list on demand (measured 2026-11-05:
# the list of 24 October names none of the four, and Windows deleted AAA Certificate
# Services from this PC on 26 October).
function Invoke-GboCheckCertStore {
    $roots = Read-GboPinnedList 'roots.json' $ROOTS_JSON_SHA256
    $inter = Read-GboPinnedList 'impacted-intermediates.json' $INTERMEDIATES_JSON_SHA256
    $problem = @(@($roots.Problem, $inter.Problem) | Where-Object { $_ }) -join '; '
    if ($problem) { Add-GboRow 'cert-store' 'unknown' $problem; return }
    try {
        $rootStore = @(Get-GboStoreThumbs 'Root' 'CurrentUser')
        $caStore = @(Get-GboStoreThumbs 'CA' 'CurrentUser')
    } catch { Add-GboRow 'cert-store' 'unknown' ('could not read the certificate stores: ' + $_.Exception.Message); return }
    $rootCount = @($roots.Entries).Count
    $present = @(@($roots.Entries) | Where-Object { $rootStore -contains (ConvertFrom-GboPem $_.pem).Thumbprint } | ForEach-Object { [string]$_.name })
    $inCa = @(@($inter.Entries) | Where-Object { $caStore -contains (ConvertFrom-GboPem $_.pem).Thumbprint }).Count
    $rootText = "none of the $rootCount incident roots"
    if ($present.Count -gt 0) { $rootText = ($present -join ', ') + " ($($present.Count) of $rootCount incident roots)" }
    $state = Get-GboRootUpdateState
    $evidence = "Root store: $rootText; CA store: $inCa of the $(@($inter.Entries).Count) incident intermediates; $($state.Text)"
    if ($present.Count -gt 0 -or $inCa -gt 0) {
        Add-GboRow 'cert-store' 'at risk' "$evidence; Python's ssl trusts both stores without Windows' block list, and Python 3.13 and newer treat an intermediate in the CA store as a trust anchor"
        return
    }
    if ($state.Problem) { Add-GboRow 'cert-store' 'unknown' $evidence; return }
    if ($state.Named -gt 0) { Add-GboRow 'cert-store' 'question' "$evidence; Windows adds a root from that list on demand, and Python's ssl would then trust it"; return }
    Add-GboRow 'cert-store' 'ok' $evidence
}

# cert-store-deny: each incident intermediate is blocked when Microsoft's automatic
# list names its key, or this PC's own Disallowed store holds it. Either counts, and
# the evidence always gives both counts.
function Invoke-GboCheckCertStoreDeny {
    $inter = Read-GboPinnedList 'impacted-intermediates.json' $INTERMEDIATES_JSON_SHA256
    if ($inter.Problem) { Add-GboRow 'cert-store-deny' 'unknown' $inter.Problem; return }
    $entries = @($inter.Entries)
    if ($entries.Count -ne $EXPECTED_INTERMEDIATES) {
        Add-GboRow 'cert-store-deny' 'unknown' "the pinned list holds $($entries.Count) certificates, not $EXPECTED_INTERMEDIATES"
        return
    }
    try { $local = @(Get-GboStoreThumbs 'Disallowed' 'LocalMachine') }
    catch { Add-GboRow 'cert-store-deny' 'unknown' ('could not read the Disallowed store: ' + $_.Exception.Message); return }
    $auto = Get-GboAutoList
    $byList = 0
    $byStore = 0
    $open = @()
    foreach ($entry in $entries) {
        $cert = ConvertFrom-GboPem $entry.pem
        $listed = [bool]$auto.Text -and $auto.Text.Contains((Get-GboKeyNeedle $cert))
        $stored = $local -contains $cert.Thumbprint
        if ($listed) { $byList++ }
        if ($stored) { $byStore++ }
        if (-not ($listed -or $stored)) { $open += [string]$entry.name }
    }
    Add-GboDenyRow $entries.Count $byList $byStore $open $auto
}

function Add-GboDenyRow([int]$Total, [int]$ByList, [int]$ByStore, [string[]]$Open, $Auto) {
    $list = "Microsoft's automatic list (refreshed $($Auto.Synced))"
    if ($Auto.Problem) { $list = "Microsoft's automatic list, which could not be read ($($Auto.Problem))" }
    $evidence = "$($Total - $Open.Count) of $Total incident intermediates blocked: $ByList by $list, $ByStore in this PC's Disallowed store"
    if ($Open.Count -eq 0) { Add-GboRow 'cert-store-deny' 'ok' $evidence; return }
    $names = @($Open | Sort-Object -Unique) -join ', '
    if ($Auto.Problem) { Add-GboRow 'cert-store-deny' 'unknown' "$evidence; not seen blocked: $names"; return }
    Add-GboRow 'cert-store-deny' 'at risk' "$evidence; not blocked: $names"
}

# ---- .NET and PowerShell 7 (Windows only): the Brotli bug in .NET ------------------------
# Get-GboDotNetRoots: the folders .NET's installers and dotnet-install.ps1 put a runtime
# in: Program Files and Program Files (x86), DOTNET_ROOT and DOTNET_ROOT(x86), and
# dotnet-install.ps1's default %LOCALAPPDATA%\Microsoft\dotnet. Each that exists counts
# once. A runtime installed anywhere else, or carried inside a self-contained app, is
# not seen, and the evidence says so.
function Get-GboDotNetRoots {
    $roots = @((Join-GboPath $env:ProgramFiles 'dotnet'), (Join-GboPath ${env:ProgramFiles(x86)} 'dotnet'), $env:DOTNET_ROOT,
        [Environment]::GetEnvironmentVariable('DOTNET_ROOT(x86)'), (Join-GboPath $env:LOCALAPPDATA 'Microsoft\dotnet'))
    $seen = @{}
    foreach ($root in $roots) {
        if (-not $root) { continue }
        $root = $root.TrimEnd('\')
        if (-not [IO.Directory]::Exists($root)) { continue }
        $key = (Resolve-GboLinkedPath $root).ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) { $seen[$key] = $true; $root }
    }
}

# Test-GboNewer A B: $true when version A is newer than version B: the higher release
# number, a release before a preview of the same number, and two previews of the same
# number in the order of their labels.
function Test-GboNewer([string]$A, [string]$B) {
    $baseA = $A.Split('-')[0]
    $baseB = $B.Split('-')[0]
    if ($baseA -cne $baseB) { return (Test-GboVge $baseA $baseB) }
    $previewA = $A.Contains('-')
    $previewB = $B.Contains('-')
    if ($previewA -ne $previewB) { return $previewB }
    if (-not $previewA) { return $false }
    return (Test-GboLabelNewer $A.Substring($A.IndexOf('-') + 1) $B.Substring($B.IndexOf('-') + 1))
}

# Test-GboLabelNewer A B: $true when pre-release label A comes after label B in the
# order SemVer 2.0.0 gives them: the dot-separated parts compared in turn, numbers as
# numbers and words in ASCII order, a number before a word, and a label after a
# shorter label it begins with. So rc.10 comes after rc.9, and rc.1 after preview.7.
function Test-GboLabelNewer([string]$A, [string]$B) {
    $partsA = $A.Split('.')
    $partsB = $B.Split('.')
    for ($i = 0; $i -lt [Math]::Min($partsA.Length, $partsB.Length); $i++) {
        $order = Compare-GboLabelPart $partsA[$i] $partsB[$i]
        if ($order -ne 0) { return ($order -gt 0) }
    }
    return ($partsA.Length -gt $partsB.Length)
}

# Compare-GboLabelPart A B: below 0, 0 or above 0 as one part of a pre-release label
# comes before part B, equals it, or comes after it.
function Compare-GboLabelPart([string]$A, [string]$B) {
    $style = [Globalization.NumberStyles]::None
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $numberA = [long]0
    $numberB = [long]0
    $isNumberA = [long]::TryParse($A, $style, $culture, [ref]$numberA)
    $isNumberB = [long]::TryParse($B, $style, $culture, [ref]$numberB)
    if ($isNumberA -and $isNumberB) { return $numberA.CompareTo($numberB) }
    if ($isNumberA) { return -1 }
    if ($isNumberB) { return 1 }
    return [string]::CompareOrdinal($A, $B)
}

# Get-GboDotNetVerdict VERSION: the status of one Microsoft.NETCore.App version, and what
# the evidence says about its floor. A preview of 8.0, 9.0 or 10.0 is older than the fix,
# which came in a patch release.
function Get-GboDotNetVerdict([string]$Version) {
    $channel = (@($Version.Split('-')[0].Split('.'))[0..1]) -join '.'
    if ($channel -ceq '11.0') { return (Get-GboDotNet11Verdict $Version) }
    $floors = @{ '8.0' = $FLOOR_DOTNET_8; '9.0' = $FLOOR_DOTNET_9; '10.0' = $FLOOR_DOTNET_10 }
    if ($floors.ContainsKey($channel)) {
        $floor = $floors[$channel]
        $note = ''
        if (@('8.0', '9.0') -contains $channel) { $note = "; Microsoft ends support for $channel on 2026-11-10" }
        if ($Version.Contains('-')) { return @('at risk', "a preview of .NET $channel, older than the fixed $floor$note") }
        return @((Get-GboStatusFor $Version $floor), "floor $floor$note")
    }
    $major = 0
    if ([int]::TryParse($channel.Split('.')[0], [ref]$major) -and $major -le 7) { return @('at risk', "no fixed release: .NET $channel is out of support") }
    return @('unknown', "no floor is known for .NET $channel")
}

# Get-GboDotNet11Verdict VERSION: .NET 11 carries the fix from release candidate 3
# (Microsoft's 11.0 release metadata lists the CVE for rc.3 and not for rc.2, read
# 2026-11-05), and every 11.0 release is built after that candidate. Its alphas,
# previews and earlier candidates are at risk. A pre-release label of another kind
# cannot be placed against rc.3.
function Get-GboDotNet11Verdict([string]$Version) {
    if (-not $Version.Contains('-')) { return @('ok', 'a release after release candidate 3, which carries the fix') }
    if ($Version -match '^11\.0\.0-rc\.(\d+)(\.|$)' -and [int]$Matches[1] -ge 3) { return @('ok', 'release candidate 3 and later carry the fix') }
    if ($Version -match '^11\.0\.0-(alpha|preview|rc)\.') { return @('at risk', 'a preview before release candidate 3, which carries the fix') }
    return @('unknown', 'a pre-release build that cannot be placed against release candidate 3, which carries the fix')
}

# Invoke-GboCheckDotNet: one row per .NET channel in each install folder, judged by its
# newest version, because "The host chooses the latest patch version installed on the
# machine" (Microsoft Learn, .NET version selection, read 2026-11-05); the older patches
# beside it are named. The x64 and x86 folders are judged apart.
function Invoke-GboCheckDotNet {
    $any = $false
    foreach ($root in @(Get-GboDotNetRoots)) {
        $shared = Join-Path $root 'shared\Microsoft.NETCore.App'
        if (-not [IO.Directory]::Exists($shared)) { continue }
        $versions = @([IO.Directory]::GetDirectories($shared) | ForEach-Object { [IO.Path]::GetFileName($_) } | Where-Object { $_ -match '^\d+\.\d+\.\d+' })
        $channels = @($versions | ForEach-Object { (@($_.Split('-')[0].Split('.'))[0..1]) -join '.' } | Sort-Object -Unique { [version]$_ })
        foreach ($channel in $channels) {
            $any = $true
            $inChannel = @($versions | Where-Object { ((@($_.Split('-')[0].Split('.'))[0..1]) -join '.') -ceq $channel })
            $newest = $inChannel[0]
            foreach ($v in $inChannel) { if (Test-GboNewer $v $newest) { $newest = $v } }
            $older = @($inChannel | Where-Object { $_ -cne $newest } | Sort-Object { [version]($_.Split('-')[0]) })
            $verdict = Get-GboDotNetVerdict $newest
            $evidence = "$root .NET $newest"
            if ($older.Count -gt 0) { $evidence += " (older versions beside it: $($older -join ', '))" }
            Add-GboRow 'dotnet' $verdict[0] "$evidence; $($verdict[1]); CVE-2026-111497, the Brotli bug in .NET; self-contained apps carry their own runtime and are not seen"
        }
    }
    if (-not $any) { Add-GboRow 'dotnet' 'n/a' 'no .NET runtime in Program Files, Program Files (x86), DOTNET_ROOT or LOCALAPPDATA\Microsoft\dotnet' }
}

# Get-GboPwshCandidates: each PowerShell 7 that can be run by full path: the MSI's folders
# (7 and 7-preview; "Beginning with the PowerShell 7.7.0 release, there is no MSI package
# available", Microsoft Learn, read 2026-11-05), the Microsoft Store package's aliases,
# and the dotnet global tool.
function Get-GboPwshCandidates {
    $paths = @((Join-GboPath $env:ProgramFiles 'PowerShell\7\pwsh.exe'), (Join-GboPath $env:ProgramFiles 'PowerShell\7-preview\pwsh.exe'),
        (Join-GboPath $env:LOCALAPPDATA 'Microsoft\WindowsApps\pwsh.exe'), (Join-GboPath $env:LOCALAPPDATA 'Microsoft\WindowsApps\pwsh-preview.exe'),
        (Join-GboPath $env:USERPROFILE '.dotnet\tools\pwsh.exe'))
    foreach ($path in $paths) { if ($path -and [IO.File]::Exists($path)) { $path } }
}

# Get-GboPwshFloor MINOR: the fixed release for a PowerShell 7 minor version, 'none' for
# 7.0 to 7.3, which are out of support, or '' past the table.
function Get-GboPwshFloor([string]$Minor) {
    $floors = @{ '7.4' = $FLOOR_PWSH_74; '7.5' = $FLOOR_PWSH_75; '7.6' = $FLOOR_PWSH_76 }
    if ($floors.ContainsKey($Minor)) { return $floors[$Minor] }
    if (@('7.0', '7.1', '7.2', '7.3') -contains $Minor) { return 'none' }
    return ''
}

# Invoke-GboCheckPwsh: one row per PowerShell 7, from what its pwsh -v prints (measured
# 2026-11-05: "PowerShell 7.6.9", and it writes nothing). PowerShell 7 carries its own
# .NET, so its fix comes in a PowerShell release. A preview has no floor.
function Invoke-GboCheckPwsh {
    $found = @(Get-GboPwshCandidates)
    if ($found.Count -eq 0) { Add-GboRow 'pwsh' 'n/a' 'no PowerShell 7 (MSI, Microsoft Store or dotnet tool)'; return }
    $quiet = @{ POWERSHELL_TELEMETRY_OPTOUT = '1'; POWERSHELL_UPDATECHECK = 'Off' }
    $tail = 'CVE-2026-111497, the Brotli bug in the .NET that PowerShell 7 carries'
    foreach ($pwsh in $found) {
        $line = Get-GboFirstLine (Invoke-GboNative -Path $pwsh -ArgumentList @('-v') -TimeoutSeconds 30 -Environment $quiet).StdOut
        if ($line -notmatch '^PowerShell (\d+\.\d+\.\d+)(\S*)$') { Add-GboRow 'pwsh' 'unknown' "${pwsh}: does not answer -v"; continue }
        $version = $Matches[1]
        $suffix = $Matches[2]
        $minor = (@($version.Split('.'))[0..1]) -join '.'
        $floor = Get-GboPwshFloor $minor
        if ($suffix) { Add-GboRow 'pwsh' 'unknown' "$pwsh PowerShell $version$suffix; a preview, with no fixed release to compare against; $tail"; continue }
        if ($floor -ceq 'none') { Add-GboRow 'pwsh' 'at risk' "$pwsh PowerShell $version; no fixed release: $minor is out of support; $tail"; continue }
        if (-not $floor) { Add-GboRow 'pwsh' 'unknown' "$pwsh PowerShell $version; no floor is known for $minor; $tail"; continue }
        Add-GboRow 'pwsh' (Get-GboStatusFor $version $floor) "$pwsh PowerShell $version; floor $floor; $tail"
    }
}

# ---- this Windows and its WSL (bash: check_os) -------------------------------------------
# Get-GboWindowsReadings: the version Windows records for itself, and the newest
# update Windows Update installed. ProductName is not read: it still says
# "Windows 10" on Windows 11 (measured 2026-11-03 on build 26200).
function Get-GboWindowsReadings {
    $cv = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $display = Get-GboProperty $cv 'DisplayVersion'
    if (-not $display) { $display = Get-GboProperty $cv 'ReleaseId' }
    $newest = ''
    $problem = ''
    try { $newest = Get-GboNewestUpdate } catch { $problem = $_.Exception.Message }
    return [pscustomobject]@{
        Build = [string](Get-GboProperty $cv 'CurrentBuildNumber')
        Ubr = [string](Get-GboProperty $cv 'UBR')
        DisplayVersion = [string]$display
        Edition = [string](Get-GboProperty $cv 'EditionID')
        NewestUpdate = $newest
        Problem = $problem
    }
}

# Get-GboNewestUpdate: the date of the newest update that Windows Update's own
# history records as installed (operation 1; result 2 succeeded or 3 succeeded
# with errors), or '' when it records none. The history is read on this PC.
function Get-GboNewestUpdate {
    $searcher = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
    $count = $searcher.GetTotalHistoryCount()
    if ($count -eq 0) { return '' }
    $installed = @($searcher.QueryHistory(0, [Math]::Min($count, 100)) |
        Where-Object { $_.Operation -eq 1 -and @(2, 3) -contains $_.ResultCode } | Sort-Object Date -Descending)
    if ($installed.Count -eq 0) { return '' }
    return $installed[0].Date.ToString('yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
}

# bash: check_os. The row has no floor: it reports the build number an advisory's
# fixed build is compared against.
function Invoke-GboCheckWindows {
    $r = Get-GboWindowsReadings
    $evidence = "Windows build $($r.Build).$($r.Ubr), version $($r.DisplayVersion), edition $($r.Edition)"
    if ($r.Problem) { $evidence += "; Windows Update history could not be read: $($r.Problem)" }
    elseif ($r.NewestUpdate) { $evidence += "; newest update installed $($r.NewestUpdate)" }
    else { $evidence += '; Windows Update history records no installed update' }
    Add-GboRow 'windows' 'info' $evidence
}

# Get-GboWslDistros [ROOT]: the WSL distributions registered for this account,
# each as its name and WSL version. Microsoft's WSL source names the key and its
# values: diagnostics/collect-wsl-logs.ps1 exports
# HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss, and DistributionRegistration.h
# reads DistributionName and Version from each distribution's subkey. Reading the
# key needs no WSL program to run. A subkey without a name is not a distribution.
function Get-GboWslDistros([string]$Root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss') {
    if (-not (Test-Path -LiteralPath $Root)) { return }
    foreach ($key in @(Get-ChildItem -LiteralPath $Root)) {
        $values = Get-ItemProperty -LiteralPath $key.PSPath
        $name = [string](Get-GboProperty $values 'DistributionName')
        if (-not $name) { continue }
        $version = Get-GboProperty $values 'Version'
        if ($null -ne $version) { $name += " (WSL $version)" }
        $name
    }
}

# bash has no WSL row: each distribution is a Linux system of its own, which the
# bash script checks from the inside.
function Invoke-GboCheckWsl {
    $distros = @(Get-GboWslDistros)
    if ($distros.Count -eq 0) { Add-GboRow 'wsl' 'n/a' 'no WSL distribution is registered for this account'; return }
    Add-GboRow 'wsl' 'info' ("$($distros.Count) WSL distribution(s) registered for this account: " + ($distros -join ', ') + '; each is a Linux system of its own: run the bash grip-black-october check inside each one')
}

# ---- the network run: the certificate stores it watches (Windows only) -----------------
# A program that checks a certificate through Windows can make Windows store a
# certificate as a side effect: an intermediate the server sent, or a root from
# Microsoft's list. Python 3.13 and newer treat an intermediate in the user's CA
# store as a trust anchor (VERIFY_X509_PARTIAL_CHAIN is on by default there), so
# such a side effect can change what another program trusts. check -Network reads
# these six stores before its first connection, and again after every connection.
# The CurrentUser views include the machine's stores; the LocalMachine ones show
# where a certificate sits, and so which removal command applies.
$GboWatchedStores = @('CurrentUser\Root', 'CurrentUser\CA', 'CurrentUser\TrustedPeople',
    'LocalMachine\Root', 'LocalMachine\AuthRoot', 'LocalMachine\CA')

# Test-GboStoreMissing ERROR: $true when a store could not be opened only because it
# does not exist yet: CryptographicException 0x80070002, "cannot find the file"
# (measured 2026-11-05 in 5.1 and 7). OpenExistingOnly never creates the store.
function Test-GboStoreMissing($ErrorRecord) {
    $e = $ErrorRecord.Exception
    while ($null -ne $e) {
        # -2147024894 is 0x80070002 as a signed 32-bit number, which is what HResult holds.
        if ($e -is [Security.Cryptography.CryptographicException] -and $e.HResult -eq -2147024894) { return $true }
        $e = $e.InnerException
    }
    return $false
}

# Get-GboStoreSnapshot: every certificate in the watched stores, keyed
# "STORE|THUMBPRINT", so a certificate that two physical stores hold behind one view
# counts once (measured 2026-11-05: the Root view lists 91 entries for 89
# certificates). A store that does not exist yet is empty, so a certificate Windows
# puts there later counts as new; a store that fails in any other way is named in
# Unreadable, so it can never pass as empty.
function Get-GboStoreSnapshot {
    $entries = @{}
    $unreadable = @()
    foreach ($spec in $GboWatchedStores) {
        $location, $name = $spec.Split('\')
        $store = New-Object Security.Cryptography.X509Certificates.X509Store($name, $location)
        try { $store.Open([Security.Cryptography.X509Certificates.OpenFlags]'ReadOnly, OpenExistingOnly') }
        catch {
            if (-not (Test-GboStoreMissing $_)) { $unreadable += $spec }
            continue
        }
        try {
            foreach ($cert in @($store.Certificates)) {
                $entries[$spec + '|' + $cert.Thumbprint] = [pscustomobject]@{
                    Store = $spec; Thumbprint = $cert.Thumbprint; Subject = $cert.Subject; Sha256 = (Get-GboSha256 $cert.RawData)
                }
            }
        } finally { $store.Close() }
    }
    return [pscustomobject]@{ Entries = $entries; Unreadable = $unreadable }
}

# Get-GboCertEvents SINCE: Windows' own certificate-update events in the Application
# log since SINCE: 4097 a root added, 4108 a root deleted, 4109 a root's properties
# updated, 4111 the trusted list updated, 4112 the distrusted list updated. In 4097,
# 4108 and 4109 the first field is the subject and the second the SHA-1 thumbprint
# (read 2026-11-05). No event at all is an empty answer, known by its error id and
# never by its message, which is in the Windows display language.
function Get-GboCertEvents([datetime]$Since) {
    $filter = @{ LogName = 'Application'; ProviderName = 'Microsoft-Windows-CAPI2'; Id = @(4097, 4108, 4109, 4111, 4112); StartTime = $Since }
    try { $records = @(Get-WinEvent -FilterHashtable $filter) }
    catch {
        if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound,*') { return }
        throw
    }
    foreach ($record in $records) {
        $thumb = ''
        if (@(4097, 4108, 4109) -contains $record.Id -and @($record.Properties).Count -gt 1) {
            $thumb = ([string]$record.Properties[1].Value).ToUpperInvariant()
        }
        [pscustomobject]@{ Id = [int]$record.Id; Thumbprint = $thumb }
    }
}

# New-GboWatch: the snapshot taken before the network run's first connection. The
# watch also carries the run's probe folder, the incident certificates' digests, and
# whether the probe has stopped.
function New-GboWatch {
    $first = Get-GboStoreSnapshot
    return [pscustomobject]@{
        Last = $first; Since = [DateTime]::Now.AddSeconds(-1); Unreadable = @($first.Unreadable)
        Added = [System.Collections.Generic.List[object]]::new()
        Folder = ''; Digests = @{}; Stopped = $false
    }
}

# Update-GboWatch WATCH CLIENT TARGET: re-read the stores after CLIENT connected to
# TARGET, and return each certificate that was not there before. Each is kept with
# the connection it followed, and with whether Windows logged adding it (event 4097
# with its thumbprint); without that event, another program or a policy may have
# added it during the run.
function Update-GboWatch($Watch, [string]$Client, [string]$Target) {
    if ($null -eq $Watch) { throw 'internal error: Update-GboWatch needs the watch New-GboWatch made' }
    $now = Get-GboStoreSnapshot
    $logged = @(Get-GboCertEvents $Watch.Since | Where-Object { $_.Id -eq 4097 } | ForEach-Object { $_.Thumbprint })
    $Watch.Since = [DateTime]::Now.AddSeconds(-1)
    $new = @()
    foreach ($key in @($now.Entries.Keys)) {
        if ($Watch.Last.Entries.ContainsKey($key)) { continue }
        $entry = $now.Entries[$key]
        $record = [pscustomobject]@{
            Store = $entry.Store; Thumbprint = $entry.Thumbprint; Subject = $entry.Subject; Sha256 = $entry.Sha256
            Client = $Client; Target = $Target; LoggedByWindows = ($logged -ccontains $entry.Thumbprint)
        }
        $Watch.Added.Add($record)
        $new += $record
    }
    $Watch.Unreadable = @(@($Watch.Unreadable) + @($now.Unreadable) | Sort-Object -Unique)
    $Watch.Last = $now
    return $new
}

# ---- the network run: the pinned lists it uses ---------------------------------------------
# ConvertFrom-GboPinnedBytes BYTES PIN ORIGIN: the entries of one incident list, from
# bytes whose sha256 must be PIN. The hash is taken of the raw bytes, never of decoded
# text, so a copy with CRLF line ends hashes as what it is; the entries are parsed
# from those same bytes, so nothing can change between the check and the use.
function ConvertFrom-GboPinnedBytes([byte[]]$Bytes, [string]$Pin, [string]$Origin) {
    $hash = Get-GboSha256 $Bytes
    if ($hash -cne $Pin) {
        return [pscustomobject]@{ Entries = @(); Problem = "$Origin has sha256 $hash ($($Bytes.Length) bytes), not the pinned ${Pin}: review the change before trusting it" }
    }
    # Windows PowerShell 5.1 returns a JSON list as one object: assign first, then wrap.
    $parsed = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString($Bytes).TrimStart([char]0xFEFF))
    return [pscustomobject]@{ Entries = @($parsed); Problem = '' }
}

# Get-GboInnermostMessage EXCEPTION: the message of the innermost exception, the one
# that says what actually failed.
function Get-GboInnermostMessage($Exception) {
    $e = $Exception
    while ($null -ne $e.InnerException) { $e = $e.InnerException }
    return $e.Message
}

# Invoke-GboFetchBytes URL [CAP] [TIMEOUT]: the bytes of one download, read in this
# process by .NET. No redirect is followed, nothing is decompressed, an answer above
# CAP bytes is refused, and the process-wide TLS settings are left as they are.
# Returns Bytes, or a Problem that says why not.
function Invoke-GboFetchBytes([string]$Url, [int]$Cap = 1048576, [int]$TimeoutMs = 30000) {
    $result = [pscustomobject]@{ Bytes = $null; Problem = '' }
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.AllowAutoRedirect = $false
    $request.AutomaticDecompression = [Net.DecompressionMethods]::None
    $request.Timeout = $TimeoutMs
    $request.ReadWriteTimeout = $TimeoutMs
    try { $response = $request.GetResponse() }
    catch { $result.Problem = "could not fetch ${Url}: " + (Get-GboInnermostMessage $_.Exception); return $result }
    try {
        $code = [int]$response.StatusCode
        if ($code -ne 200) { $result.Problem = "$Url answered HTTP $code, and a redirect is never followed"; return $result }
        $stream = $response.GetResponseStream()
        $memory = New-Object IO.MemoryStream
        $buffer = New-Object byte[] 65536
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $memory.Write($buffer, 0, $read)
            if ($memory.Length -gt $Cap) { $result.Problem = "$Url sent more than $Cap bytes, so it was refused"; return $result }
        }
        $result.Bytes = $memory.ToArray()
        return $result
    } finally { $response.Close() }
}

# Import-GboPinnedLists WATCH: this run's two pinned lists. A reviewed copy in the
# work folder that still matches its pin is used with no connection. Otherwise the
# list is fetched into memory from the incident page and never saved, and the stores
# are re-read after the fetch, because the fetch checks a certificate through
# Windows. A list that cannot be had keeps both reasons.
function Import-GboPinnedLists($Watch) {
    foreach ($name in @($GboPinnedLists.Keys)) {
        $local = Read-GboPinnedList $name $GboPinnedLists[$name]
        if (-not $local.Problem) { $GboPinnedCache[$name] = $local; continue }
        $url = "$INCIDENT_BASE/$name"
        $fetched = Invoke-GboFetchBytes $url
        [void](Update-GboWatch $Watch 'the pinned-list fetch' $url)
        if ($fetched.Problem) {
            $GboPinnedCache[$name] = [pscustomobject]@{ Entries = @(); Problem = $local.Problem + '; ' + $fetched.Problem }
            continue
        }
        $GboPinnedCache[$name] = ConvertFrom-GboPinnedBytes $fetched.Bytes $GboPinnedLists[$name] "the copy fetched from $url"
    }
}

# ---- the network run: its one folder, and its start -------------------------------------
# New-GboProbeFolder: the folder check -Network writes in, new under the user's TEMP.
# Remove-GboProbeFolder deletes it at the end of the run, whatever happened.
function New-GboProbeFolder {
    $path = Join-Path ([IO.Path]::GetTempPath()) ('gbo-probe.' + [IO.Path]::GetRandomFileName().Replace('.', ''))
    [void][IO.Directory]::CreateDirectory($path)
    return $path
}

function Remove-GboProbeFolder([string]$Path) {
    if ($Path -and [IO.Directory]::Exists($Path)) { [IO.Directory]::Delete($Path, $true) }
}

# Start-GboNetworkRun FOLDER: the store snapshot first, before the run's first
# connection, then this run's pinned lists and the incident certificates' digests.
# Returns the watch the probe keeps updating; FOLDER is the probe folder.
function Start-GboNetworkRun([string]$Folder = '') {
    $GboPinnedCache.Clear()
    $watch = New-GboWatch
    $watch.Folder = $Folder
    Import-GboPinnedLists $watch
    $watch.Digests = Get-GboIncidentDigests
    return $watch
}

# ---- the network run: the probe engine (bash: verdict, check_trust_probe) ---------------
# The incident page runs 14 test sites, here in bash's order; each presents a chain
# through one of the compromised intermediates, so a safe client rejects all 14. The
# control is the incident page itself, which a working client must load.
$GboProbeHosts = @('dv-g3b', 'dv-g3b-aaa', 'dv-g3c', 'dv-g3c-aaa', 'ov-g3b', 'ov-g3b-aaa', 'ov-g3c', 'ov-g3c-aaa',
    'ev-g3b', 'ev-g3b-aaa', 'ev-g3c', 'ev-g3c-aaa', 'dv-g3b-noct', 'dv-g3b-aaa-noct')

# Get-GboProbeUrls: the control first, then the 14 test sites (bash: GOOD_URL, HOSTS).
function Get-GboProbeUrls {
    "$INCIDENT_BASE/"
    foreach ($h in $GboProbeHosts) { "https://${h}-emudhra.we-have-a-problem.com/" }
}

# Get-GboIncidentDigests: the SHA-256 of each incident certificate this run's pinned
# lists hold (4 roots, 16 intermediates), taken of the certificates themselves.
function Get-GboIncidentDigests {
    $digests = @{}
    foreach ($name in @($GboPinnedLists.Keys)) {
        foreach ($entry in @((Read-GboPinnedList $name $GboPinnedLists[$name]).Entries)) {
            $digests[(Get-GboSha256 (ConvertFrom-GboPem $entry.pem).RawData)] = [string]$entry.name
        }
    }
    return $digests
}

# Test-GboProbeMustStop ADDED DIGESTS: $true when a certificate that appeared is one of
# the incident's. When the pinned lists could not be had, DIGESTS is empty and no new
# certificate can be told apart, so then any new certificate stops the probe.
function Test-GboProbeMustStop($Added, $Digests) {
    foreach ($record in @($Added)) {
        if ($Digests.Count -eq 0 -or $Digests.ContainsKey($record.Sha256)) { return $true }
    }
    return $false
}

# Add-GboProbeVerdict LABEL ANSWERS: bash's verdict for one client, in bash's words.
# ANSWERS holds the control's answer first, then one per test site: ok (trusted),
# cert (rejected for its certificate) or err. The control must load; then any trusted
# site puts the client at risk, and only 14 rejections of 14 make it safe.
function Add-GboProbeVerdict([string]$Label, [string[]]$Answers) {
    if ($Answers.Count -lt 1 -or $Answers[0] -cne 'ok') {
        Add-GboRow 'trust-probe' 'unknown' "${Label}: control failed (no network, or the tool is broken)"
        return
    }
    $sites = @($Answers | Select-Object -Skip 1)
    $n = $GboProbeHosts.Count
    $t = @($sites | Where-Object { $_ -ceq 'ok' }).Count
    $r = @($sites | Where-Object { $_ -ceq 'cert' }).Count
    if ($t -gt 0) { Add-GboRow 'trust-probe' 'at risk' "${Label}: TRUSTS the bad chain (trusted $t, rejected $r of $n)"; return }
    if ($r -eq $n) { Add-GboRow 'trust-probe' 'ok' "${Label}: safe (rejected $r of $n)"; return }
    Add-GboRow 'trust-probe' 'unknown' "${Label}: trusted $t, rejected $r, other $($n - $t - $r) of $n"
}

# New-GboProbeClient KIND PATH LABEL: one client the probe runs, by kind; the kind
# decides which probe Invoke-GboProbeOnce runs. Root is the SHA-256 of the root the
# client's last chain ended in, for the clients that can say (PowerShell's .NET).
function New-GboProbeClient([string]$Kind, [string]$Path, [string]$Label) {
    return [pscustomobject]@{ Kind = $Kind; Path = $Path; Label = $Label; Root = '' }
}

# Invoke-GboProbeClient WATCH CLIENT: one client's 15 connections, one at a time, with
# the stores re-read after every connection, then bash's verdict. A client that would
# start after the probe has stopped connects nowhere and says so; the client that was
# connecting when an incident certificate appeared stops at once. A client whose
# control chain shows an HTTPS-inspecting product stops after the control: the 14
# verdicts would be that product's, and each would be one more connection.
function Invoke-GboProbeClient($Watch, $Client) {
    $label = $Client.Label
    if ($Watch.Stopped) {
        Add-GboRow 'trust-probe' 'unknown' "${label}: not probed: the probe stopped after an incident certificate appeared"
        return
    }
    $answers = @()
    foreach ($url in @(Get-GboProbeUrls)) {
        $answers += [string](Invoke-GboProbeOnce $Client $url $Watch.Folder)
        if (Test-GboProbeMustStop @(Update-GboWatch $Watch $label $url) $Watch.Digests) {
            $Watch.Stopped = $true
            Add-GboRow 'trust-probe' 'unknown' "${label}: stopped after ${url}: an incident certificate appeared (see trust-probe-stores)"
            return
        }
        if ($answers.Count -eq 1 -and $answers[0] -ceq 'ok' -and (Test-GboInspected $Client)) {
            Add-GboRow 'trust-probe' 'unknown' "${label}: an HTTPS-inspecting product answers this client: the control's chain ends in a root that Microsoft's trusted list does not name, so its verdicts would be that product's; the 14 test sites were not tried"
            return
        }
    }
    Add-GboProbeVerdict $label $answers
}

# Get-GboProbeAnswer ANSWER: the first line of a probe child's output when it is ok,
# cert or err; anything else, a timeout included, is err.
function Get-GboProbeAnswer($Answer) {
    $line = Get-GboFirstLine $Answer.StdOut
    if (-not $Answer.TimedOut -and @('ok', 'cert', 'err') -ccontains $line) { return $line }
    return 'err'
}

# Get-GboSetNames NAMES: which of the environment variables NAMES are set, by name only,
# for evidence: their values can hold paths and options that are nobody else's business.
function Get-GboSetNames([string[]]$Names) {
    $set = @($Names | Where-Object { [Environment]::GetEnvironmentVariable($_) })
    if ($set.Count -eq 0) { return '' }
    return '; set: ' + ($set -join ', ')
}

# ---- the network run: clients with their own trust lists (bash's group, first) -------------
# Each probe child runs inside the probe folder, so a module or file planted in the
# folder the user started from (an ssl.py in a cloned repository, say) is never found.

# bash's node probe, plus a request for the identity encoding. A node below its floor
# carries the Brotli decoder bug, and asking for no compression keeps a well-behaved
# server from sending Brotli; it cannot stop a hostile server, which may send it
# anyway. Measured 2026-11-05: node 22.18.0 sends "gzip, deflate" by default, and
# "identity" with this header.
$GboNodeProbe = 'fetch(process.argv[1], { headers: { "accept-encoding": "identity" } }).then(() => console.log("ok")).catch(e => console.log(/CERT|ISSUER|SIGNATURE|SELF_SIGNED/.test(e.cause?.code ?? "") ? "cert" : "err"))'

# bash's Python probe, unchanged: urllib with Python's own ssl.
$GboPythonProbe = @'
import ssl,sys,urllib.request as u
try: u.urlopen(sys.argv[1], timeout=15); print("ok")
except Exception as e: print("ok" if hasattr(e, "code") else "cert" if isinstance(getattr(e, "reason", e), ssl.SSLCertVerificationError) else "err")
'@

# bash's Java probe, unchanged: Java's own trust store, through HttpURLConnection.
$GboJavaProbe = @(
    'public class P { public static void main(String[] a) { try { java.net.HttpURLConnection c = (java.net.HttpURLConnection) java.net.URI.create(a[0]).toURL().openConnection();',
    'c.setConnectTimeout(15000); c.setReadTimeout(15000); c.getResponseCode(); System.out.println("ok"); } catch (Exception e) { for (Throwable t = e; t != null; t = t.getCause())',
    'if (t instanceof java.security.cert.CertificateException || t instanceof java.security.cert.CertPathValidatorException || t instanceof java.security.cert.CertPathBuilderException) { System.out.println("cert"); return; } System.out.println("err"); } } }'
) -join "`n"

# Invoke-GboProbeOnce CLIENT URL FOLDER: one connection by one client, as bash makes it
# (uv reads the package name from a file instead of stdin). Java runs P.java as a
# single source file (Java 11 and newer); -XX:-UsePerfData keeps the JVM from writing
# its performance files under TEMP.
function Invoke-GboProbeOnce($Client, [string]$Url, [string]$Folder) {
    $path = $Client.Path
    switch ($Client.Kind) {
        'curl-own' { return (Get-GboCurlOwnAnswer $path $Url $Folder) }
        'python' { return (Get-GboProbeAnswer (Invoke-GboNative -Path $path -ArgumentList @('-c', $GboPythonProbe, $Url) -TimeoutSeconds 40 -WorkingDirectory $Folder)) }
        'node' { return (Get-GboProbeAnswer (Invoke-GboNative -Path $path -ArgumentList @('-e', $GboNodeProbe, $Url) -TimeoutSeconds 40 -WorkingDirectory $Folder)) }
        'uv' { return (Get-GboUvAnswer $path $Url $Folder) }
        'java' { return (Get-GboProbeAnswer (Invoke-GboNative -Path $path -ArgumentList @('-XX:-UsePerfData', 'P.java', $Url) -TimeoutSeconds 60 -WorkingDirectory $Folder)) }
        'curl-schannel' { return (Get-GboCurlSchannelAnswer $path $Url $Folder) }
        'dotnet' { return (Get-GboDotNetAnswer $Client $Url $Folder) }
        'pip' { return (Get-GboPipAnswer $path $Url $Folder) }
        'git' { return (Get-GboGitAnswer $path $Url $Folder) }
    }
    throw "internal error: no probe for a client of kind '$($Client.Kind)'"
}

# Get-GboCurlOwnAnswer CURL URL FOLDER: bash's curl probe: exit 0 trusted, 60 rejected.
function Get-GboCurlOwnAnswer([string]$Curl, [string]$Url, [string]$Folder) {
    $answer = Invoke-GboNative -Path $Curl -ArgumentList @('-sS', '-m', '15', '-o', 'NUL', $Url) -TimeoutSeconds 40 -WorkingDirectory $Folder
    if ($answer.ExitCode -eq 0) { return 'ok' }
    if ($answer.ExitCode -eq 60) { return 'cert' }
    return 'err'
}

# Get-GboUvAnswer UV URL FOLDER: bash's uv probe: a response line means the chain was
# trusted, "invalid peer certificate" that it was rejected.
function Get-GboUvAnswer([string]$Uv, [string]$Url, [string]$Folder) {
    $arguments = @('pip', 'compile', 'req.in', '-vv', '--no-cache', '--no-index', '--no-python-downloads', '--find-links', $Url)
    $answer = Invoke-GboNative -Path $Uv -ArgumentList $arguments -TimeoutSeconds 60 -WorkingDirectory $Folder
    $text = $answer.StdOut + "`n" + $answer.StdErr
    if ($text -match 'Received response .*status') { return 'ok' }
    if ($text -match 'invalid peer certificate') { return 'cert' }
    return 'err'
}

# Get-GboPythonProbeTargets FOLDER: each Python that runs, with its version and trust
# group. A Python whose ssl.SSLContext is not the standard library's has truststore
# injected, so it checks certificates through Windows (the windows group); any other
# checks them against what OpenSSL loaded (the own group).
function Get-GboPythonProbeTargets([string]$Folder) {
    $code = 'import ssl,sys;print(sys.version.split()[0], ssl.SSLContext.__module__)'
    foreach ($python in @(Get-GboPythonCandidates)) {
        $line = Get-GboFirstLine (Invoke-GboNative -Path $python -ArgumentList @('-c', $code) -TimeoutSeconds 30 -WorkingDirectory $Folder).StdOut
        if ($line -notmatch '^(\d+\.\d+\S*) (\S+)$') { continue }
        $group = 'windows'
        if ($Matches[2] -ceq 'ssl') { $group = 'own' }
        [pscustomobject]@{ Path = $python; Version = $Matches[1]; Group = $group }
    }
}

# Test-GboUvSystemCerts: $true when uv is set to check certificates through Windows:
# UV_SYSTEM_CERTS (uv 0.11's --help names it) or the older UV_NATIVE_TLS set to a true
# value, or system-certs or native-tls set to true in uv's user or system uv.toml.
function Test-GboUvSystemCerts {
    foreach ($name in @('UV_SYSTEM_CERTS', 'UV_NATIVE_TLS')) {
        $value = [string][Environment]::GetEnvironmentVariable($name)
        if (@('1', 'true', 'yes', 'on') -contains $value.Trim().ToLowerInvariant()) { return $true }
    }
    foreach ($file in @((Join-GboPath $env:APPDATA 'uv\uv.toml'), (Join-GboPath $env:ProgramData 'uv\uv.toml'))) {
        if (-not $file -or -not [IO.File]::Exists($file)) { continue }
        if ([IO.File]::ReadAllText($file) -match '(?m)^\s*(system-certs|native-tls)\s*=\s*true\b') { return $true }
    }
    return $false
}

# Get-GboJavaCandidates: JAVA_HOME's java.exe, then each java.exe on PATH, one per real
# program.
function Get-GboJavaCandidates {
    $found = @()
    $javaHome = Join-GboPath $env:JAVA_HOME 'bin\java.exe'
    if ($javaHome -and [IO.File]::Exists($javaHome)) { $found += $javaHome }
    $found += @(Get-GboEachOnPath 'java.exe')
    $seen = @{}
    foreach ($path in $found) {
        $key = (Resolve-GboLinkedPath $path).ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) { $seen[$key] = $true; $path }
    }
}

# Get-GboCurlCandidates: Windows' own curl.exe and each one on PATH, with the TLS
# engine its curl -V names. Schannel checks certificates through Windows (the windows
# group); any other engine, OpenSSL say, checks them against its own list.
function Get-GboCurlCandidates {
    $found = @(Get-GboEachOnPath 'curl.exe')
    $system = Get-GboSystemProgram 'System32\curl.exe'
    if ($system) { $found = @($system) + $found }
    $seen = @{}
    foreach ($path in $found) {
        $key = (Resolve-GboLinkedPath $path).ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $line = Get-GboFirstLine (Invoke-GboNative -Path $path -ArgumentList @('-V') -TimeoutSeconds 15).StdOut
        $group = 'own'
        if ($line -match 'Schannel') { $group = 'windows' }
        [pscustomobject]@{ Path = $path; Version = $line; Group = $group }
    }
}

# Get-GboOwnListClients FOLDER: every client with its own trust list, in bash's order:
# curl, each Python, each node, uv, each Java. A batch-file node is never handed code,
# because cmd.exe would read its quotes again first; it gets a row of its own instead.
function Get-GboOwnListClients([string]$Folder) {
    foreach ($curl in @(Get-GboCurlCandidates | Where-Object { $_.Group -ceq 'own' })) {
        New-GboProbeClient 'curl-own' $curl.Path "curl $($curl.Path)"
    }
    foreach ($python in @(Get-GboPythonProbeTargets $Folder | Where-Object { $_.Group -ceq 'own' })) {
        New-GboProbeClient 'python' $python.Path "python $($python.Version) $($python.Path)"
    }
    $nodeNames = Get-GboSetNames @('NODE_USE_SYSTEM_CA', 'NODE_OPTIONS', 'NODE_EXTRA_CA_CERTS')
    foreach ($node in @(Get-GboNodeCandidates)) {
        if ([IO.Path]::GetExtension($node) -ne '.exe') {
            Add-GboRow 'trust-probe' 'unknown' "${node}: a batch file, which the probe never hands code"
            continue
        }
        $version = Get-GboFirstLine (Invoke-GboNative -Path $node -ArgumentList @('-v') -TimeoutSeconds 15 -WorkingDirectory $Folder).StdOut
        New-GboProbeClient 'node' $node "node $version $node$nodeNames"
    }
    if (-not (Test-GboUvSystemCerts)) {
        foreach ($uv in @(Get-GboEachOnPath 'uv.exe')) {
            $line = Get-GboFirstLine (Invoke-GboNative -Path $uv -ArgumentList @('--version') -TimeoutSeconds 15 -WorkingDirectory $Folder).StdOut
            $version = ''
            if ($line -match '^uv (\S+)') { $version = $Matches[1] }
            New-GboProbeClient 'uv' $uv "uv $version $uv"
        }
    }
    $javaNames = Get-GboSetNames @('JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS')
    foreach ($java in @(Get-GboJavaCandidates)) {
        $answer = Invoke-GboNative -Path $java -ArgumentList @('-XX:-UsePerfData', '-version') -TimeoutSeconds 30 -WorkingDirectory $Folder
        $version = ''
        if ($answer.StdErr -match '"([^"]+)"') { $version = $Matches[1] }
        New-GboProbeClient 'java' $java "java $version $java$javaNames"
    }
}

# Invoke-GboProbeOwnLists WATCH: the probe for every client with its own trust list,
# with the two files bash's uv and Java probes read put in the probe folder first.
function Invoke-GboProbeOwnLists($Watch) {
    [IO.File]::WriteAllText((Join-Path $Watch.Folder 'req.in'), "zz-nonexistent-pkg-31337`n")
    [IO.File]::WriteAllText((Join-Path $Watch.Folder 'P.java'), $GboJavaProbe)
    foreach ($client in @(Get-GboOwnListClients $Watch.Folder)) { Invoke-GboProbeClient $Watch $client }
}

# ---- the network run: clients that check certificates through Windows (second) -----------
# Get-GboSystemProgram RELATIVE: a program that ships with Windows, by full path under
# SystemRoot, or nothing when it is not there. Every lookup of one goes through here.
function Get-GboSystemProgram([string]$Relative) {
    $path = Join-GboPath $env:SystemRoot $Relative
    if ($path -and [IO.File]::Exists($path)) { return $path }
}

# Windows reports a certificate it distrusts, its own block list included, as revoked:
# measured 2026-11-05, Windows' curl.exe, Git's curl.exe and git each gave
# CRYPT_E_REVOKED (0x80092010) on all 14 test sites. These five codes mean the chain
# was rejected, by the meaning Windows itself gives them (read 2026-11-05 through
# Win32Exception): revoked; ended in an untrusted root; issued by an untrusted
# authority; explicitly marked as untrusted; no chain to a trusted root. A code that
# says revocation could not be checked, or any other code, is no verdict.
$GboSchannelRejects = @('80092010', '800B0109', '80090325', '800B0111', '800B010A')

# Get-GboSchannelAnswer TEXT: cert when TEXT names one of the codes above, else err.
function Get-GboSchannelAnswer([string]$Text) {
    if ($Text -match '\(0x([0-9A-Fa-f]{8})\)' -and $GboSchannelRejects -contains $Matches[1].ToUpperInvariant()) { return 'cert' }
    return 'err'
}

# Get-GboCurlSchannelAnswer CURL URL FOLDER: a Schannel curl: exit 0 trusted, 60
# rejected, and exit 35 read by the Windows code curl prints with it.
function Get-GboCurlSchannelAnswer([string]$Curl, [string]$Url, [string]$Folder) {
    $answer = Invoke-GboNative -Path $Curl -ArgumentList @('-sS', '-m', '15', '-o', 'NUL', $Url) -TimeoutSeconds 40 -WorkingDirectory $Folder
    if ($answer.ExitCode -eq 0) { return 'ok' }
    if ($answer.ExitCode -eq 60) { return 'cert' }
    if ($answer.ExitCode -eq 35) { return (Get-GboSchannelAnswer $answer.StdErr) }
    return 'err'
}

# Get-GboGitAnswer GIT URL FOLDER: git ls-remote with no prompt, no credential helper,
# English messages, and no look above the probe folder for a repository; it is killed
# if it outlives its time (the upstream Windows review's first point). The test sites
# are not git servers, so an HTTP answer means TLS was trusted; a Windows rejection
# code, or OpenSSL's "SSL certificate problem", means the chain was rejected.
function Get-GboGitAnswer([string]$Git, [string]$Url, [string]$Folder) {
    $answer = Invoke-GboNative -Path $Git -ArgumentList @('-c', 'credential.helper=', 'ls-remote', $Url) -TimeoutSeconds 40 -Environment (Get-GboGitQuiet $Folder) -WorkingDirectory $Folder
    $text = $answer.StdErr
    if ($answer.ExitCode -eq 0 -or $text -match "repository '[^']*' not found|returned error: \d+|not valid: is this a git repository") { return 'ok' }
    if ($text -match 'SSL certificate problem') { return 'cert' }
    return (Get-GboSchannelAnswer $text)
}

# Get-GboGitQuiet FOLDER: the variables every git call here gets.
function Get-GboGitQuiet([string]$Folder) {
    return @{ GIT_TERMINAL_PROMPT = '0'; GCM_INTERACTIVE = 'never'; LC_ALL = 'C'; GIT_CEILING_DIRECTORIES = (Split-Path -Parent $Folder) }
}

# Get-GboPipAnswer PYTHON URL FOLDER: bash's pip probe, line by line as bash's awk
# reads it: a GET answered 200 or 304 means the chain was trusted; "problem confirming
# the ssl certificate", on a line that is not about a missing ssl module, that it was
# rejected. A file that lands would land in the probe folder's dl folder.
function Get-GboPipAnswer([string]$Python, [string]$Url, [string]$Folder) {
    $arguments = @('-m', 'pip', 'download', '-vvv', '--no-cache-dir', '--retries', '0', '--timeout', '15', '--no-index', '--no-deps',
        '--only-binary=:all:', '--disable-pip-version-check', '--find-links', $Url, '-d', (Join-Path $Folder 'dl'), 'zz-nonexistent-pkg-31337')
    $answer = Invoke-GboNative -Path $Python -ArgumentList $arguments -TimeoutSeconds 60 -WorkingDirectory $Folder
    $lines = @(($answer.StdOut + "`n" + $answer.StdErr) -split "`n")
    if (@($lines | Where-Object { $_ -match '"GET [^"]*" (200|304)' }).Count -gt 0) { return 'ok' }
    $rejected = @($lines | Where-Object { $_ -match 'problem confirming the ssl certificate' -and $_ -notmatch 'module is (not available|unavailable)' })
    if ($rejected.Count -gt 0) { return 'cert' }
    return 'err'
}

# The TLS probe for PowerShell's .NET, run as a child of each PowerShell (5.1 and 7)
# from a file in the probe folder, so a hang is killed and no setting changes in this
# process. One handshake through SslStream with the default validation: the callback
# only watches, returns exactly the default decision (accept when there is no error),
# and keeps the errors and the root the chain ends in. The first line is ok, cert
# (rejected for its certificate) or err; the second names that root's SHA-256.
$GboTlsProbe = @'
param([string]$HostName)
$ErrorActionPreference = 'Stop'
$script:errors = ''
$script:root = ''
$watcher = [Net.Security.RemoteCertificateValidationCallback] {
    param($Sender, $Certificate, $Chain, $Errors)
    $script:errors = [string]$Errors
    if ($null -ne $Chain -and $Chain.ChainElements.Count -gt 0) {
        $top = $Chain.ChainElements[$Chain.ChainElements.Count - 1].Certificate
        $sha = [Security.Cryptography.SHA256]::Create()
        $script:root = -join ($sha.ComputeHash($top.RawData) | ForEach-Object { $_.ToString('x2') })
    }
    return ($Errors -eq [Net.Security.SslPolicyErrors]::None)
}
$answer = 'err'
$tcp = New-Object Net.Sockets.TcpClient
try {
    $wait = $tcp.BeginConnect($HostName, 443, $null, $null)
    if ($wait.AsyncWaitHandle.WaitOne(15000)) {
        $tcp.EndConnect($wait)
        $tcp.ReceiveTimeout = 15000
        $tcp.SendTimeout = 15000
        $tls = New-Object Net.Security.SslStream($tcp.GetStream(), $false, $watcher)
        try {
            $tls.AuthenticateAsClient($HostName)
            $answer = 'ok'
        } catch {
            if ($script:errors -and $script:errors -ne 'None') { $answer = 'cert' }
        }
    }
} catch {
    # A connection that cannot be made answers err, which is no verdict.
    $answer = 'err'
} finally {
    $tcp.Close()
}
$answer
'root=' + $script:root
'@

# Get-GboDotNetAnswer CLIENT URL FOLDER: the TLS probe in a child of one PowerShell.
# The root the chain ended in stays on the client, for the HTTPS-inspection check.
function Get-GboDotNetAnswer($Client, [string]$Url, [string]$Folder) {
    $arguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Folder 'tls-probe.ps1'), ([Uri]$Url).Host)
    $quiet = @{ POWERSHELL_TELEMETRY_OPTOUT = '1'; POWERSHELL_UPDATECHECK = 'Off' }
    $answer = Invoke-GboNative -Path $Client.Path -ArgumentList $arguments -TimeoutSeconds 40 -Environment $quiet -WorkingDirectory $Folder
    $Client.Root = ''
    if ($answer.StdOut -match '(?m)^root=([0-9a-f]{64})\s*$') { $Client.Root = $Matches[1] }
    return (Get-GboProbeAnswer $answer)
}

# Test-GboInspected CLIENT: $true when the control's chain, as the client saw it, ends
# in a root that Microsoft's trusted list does not name: then an HTTPS-inspecting
# product answers this client (measured 2026-11-05: one re-signs both PowerShell
# editions here), and its verdicts would be that product's.
function Test-GboInspected($Client) {
    if (-not $Client.Root) { return $false }
    $list = Get-GboTrustedList
    if ($list.Problem) { return $false }
    return (-not $list.Text.Contains((Get-GboDigestNeedle $Client.Root)))
}

# Get-GboPowerShells: Windows PowerShell 5.1 and PowerShell 7, each by full path: 7 is
# the first that Get-GboPwshCandidates finds.
function Get-GboPowerShells {
    $windows = Get-GboSystemProgram 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if ($windows) { [pscustomobject]@{ Path = $windows; Label = "Windows PowerShell 5.1 (.NET Framework) $windows" } }
    $pwsh = @(Get-GboPwshCandidates | Select-Object -First 1)
    if ($pwsh.Count -gt 0) { [pscustomobject]@{ Path = $pwsh[0]; Label = "PowerShell 7 (.NET) $($pwsh[0])" } }
}

# Get-GboGitCandidates FOLDER: each git on PATH, one per installation (Git for Windows
# puts cmd\git.exe and its build folder's bin\git.exe on PATH, and both name one
# --exec-path), with its version and the TLS backend its configuration names.
function Get-GboGitCandidates([string]$Folder) {
    $quiet = Get-GboGitQuiet $Folder
    $seen = @{}
    foreach ($git in @(Get-GboEachOnPath 'git.exe')) {
        $exec = Get-GboFirstLine (Invoke-GboNative -Path $git -ArgumentList @('--exec-path') -TimeoutSeconds 15 -Environment $quiet -WorkingDirectory $Folder).StdOut
        if (-not $exec -or $seen.ContainsKey($exec.ToLowerInvariant())) { continue }
        $seen[$exec.ToLowerInvariant()] = $true
        $version = (Get-GboFirstLine (Invoke-GboNative -Path $git -ArgumentList @('--version') -TimeoutSeconds 15 -Environment $quiet -WorkingDirectory $Folder).StdOut) -replace '^git version ', ''
        $backend = Get-GboFirstLine (Invoke-GboNative -Path $git -ArgumentList @('config', '--get', 'http.sslBackend') -TimeoutSeconds 15 -Environment $quiet -WorkingDirectory $Folder).StdOut
        if (-not $backend) { $backend = 'not set' }
        [pscustomobject]@{ Path = $git; Label = "git $version $git (http.sslBackend $backend)" }
    }
}

# Get-GboWindowsTrustClients FOLDER: every client that checks certificates through
# Windows, in D5's order: the Schannel curls (Windows' own first), PowerShell 5.1 and
# 7, then for each Python a truststore-injected urllib and its pip, then git.
function Get-GboWindowsTrustClients([string]$Folder) {
    foreach ($curl in @(Get-GboCurlCandidates | Where-Object { $_.Group -ceq 'windows' })) {
        New-GboProbeClient 'curl-schannel' $curl.Path "curl $($curl.Path)"
    }
    foreach ($shell in @(Get-GboPowerShells)) { New-GboProbeClient 'dotnet' $shell.Path $shell.Label }
    foreach ($python in @(Get-GboPythonProbeTargets $Folder)) {
        if ($python.Group -ceq 'windows') { New-GboProbeClient 'python' $python.Path "python $($python.Version) $($python.Path) (truststore)" }
        $pip = Get-GboFirstLine (Invoke-GboNative -Path $python.Path -ArgumentList @('-m', 'pip', '--version') -TimeoutSeconds 30 -WorkingDirectory $Folder).StdOut
        if ($pip -match '^pip (\S+)') { New-GboProbeClient 'pip' $python.Path "pip $($Matches[1]) $($python.Path)" }
    }
    foreach ($git in @(Get-GboGitCandidates $Folder)) { New-GboProbeClient 'git' $git.Path $git.Label }
}

# Invoke-GboProbeWindowsTrust WATCH: the probe for every client that checks
# certificates through Windows, with the TLS probe file put in the probe folder first.
function Invoke-GboProbeWindowsTrust($Watch) {
    [IO.File]::WriteAllText((Join-Path $Watch.Folder 'tls-probe.ps1'), $GboTlsProbe)
    foreach ($client in @(Get-GboWindowsTrustClients $Watch.Folder)) { Invoke-GboProbeClient $Watch $client }
}

# Add-GboDownloadsRow FOLDER: bash's trust-probe-downloads: the package the pip probe
# asks for does not exist, so no file may land in its download folder.
function Add-GboDownloadsRow([string]$Folder) {
    $dl = Join-Path $Folder 'dl'
    $n = 0
    if ([IO.Directory]::Exists($dl)) { $n = @([IO.Directory]::GetFiles($dl, '*', [IO.SearchOption]::AllDirectories)).Count }
    $status = 'ok'
    if ($n -gt 0) { $status = 'at risk' }
    Add-GboRow 'trust-probe-downloads' $status "$n file(s) landed in the probe's download directory (the package does not exist, so none should)"
}

# Get-GboRootUpdatePolicy: whether a policy turns off Windows' automatic update of its
# roots: the DisableRootAutoUpdate value under the AuthRoot policy key.
function Get-GboRootUpdatePolicy {
    $key = 'HKLM:\SOFTWARE\Policies\Microsoft\SystemCertificates\AuthRoot'
    if ((Test-Path -LiteralPath $key) -and (Get-GboProperty (Get-ItemProperty -LiteralPath $key) 'DisableRootAutoUpdate') -eq 1) {
        return 'a policy turns automatic root update off'
    }
    return 'automatic root update is on'
}

# Get-GboRootUpdateState: whether Windows could still add an incident root on demand:
# how many of the incident roots Microsoft's trusted list names (Named), when Windows
# last refreshed that list, and the policy, as one sentence (Text). Problem says why
# the list could not be read.
function Get-GboRootUpdateState {
    $policy = Get-GboRootUpdatePolicy
    $list = Get-GboTrustedList
    if ($list.Problem) {
        return [pscustomobject]@{ Named = 0; Problem = $list.Problem; Text = "Microsoft's trusted list could not be read ($($list.Problem)); $policy" }
    }
    $roots = @((Read-GboPinnedList 'roots.json' $ROOTS_JSON_SHA256).Entries)
    $named = @($roots | Where-Object { $list.Text.Contains((Get-GboDigestNeedle (Get-GboSha256 (ConvertFrom-GboPem $_.pem).RawData))) }).Count
    return [pscustomobject]@{ Named = $named; Problem = ''; Text = "incident roots Microsoft's trusted list still names: $named of $($roots.Count) (refreshed $($list.Synced)); $policy" }
}

function Get-GboRootUpdateText { return (Get-GboRootUpdateState).Text }

# The stores to name in a removal command, the most specific first: a certificate a
# view shows is removed from the store that holds it.
$GboRemovalOrder = @('LocalMachine\AuthRoot', 'LocalMachine\CA', 'CurrentUser\CA', 'CurrentUser\TrustedPeople',
    'LocalMachine\Root', 'CurrentUser\Root')

# Get-GboRemovalText RECORDS THUMBPRINT: the command that removes one certificate, and
# the shell it needs: a machine store needs an elevated PowerShell.
function Get-GboRemovalText($Records, [string]$Thumbprint) {
    $stores = @($Records | Where-Object { $_.Thumbprint -ceq $Thumbprint } | ForEach-Object { $_.Store })
    $store = @($GboRemovalOrder | Where-Object { $stores -contains $_ })[0]
    $shell = 'a normal PowerShell'
    if ($store -like 'LocalMachine*') { $shell = 'an elevated PowerShell' }
    return "remove it in ${shell}: Remove-Item -LiteralPath 'Cert:\$store\$Thumbprint'"
}

# Add-GboStoresRow WATCH: what appeared in the six stores during the run (Windows only).
# An incident certificate is at risk: a program that reads that store, as Python 3.13
# and newer do, now trusts its chains. Any other new root that Windows did not log
# adding is a question. Other certificates Windows cached are counted. A store that
# could not be read, or a run with no pinned lists to tell certificates apart,
# leaves the row unknown.
function Add-GboStoresRow($Watch) {
    $context = Get-GboRootUpdateText
    $added = @($Watch.Added)
    if (@($Watch.Unreadable).Count -gt 0) {
        Add-GboRow 'trust-probe-stores' 'unknown' ("could not read $(@($Watch.Unreadable) -join ', ') during the run, so it cannot say what appeared there; " + $context)
        return
    }
    if ($added.Count -gt 0 -and $Watch.Digests.Count -eq 0) {
        $first = $added[0]
        Add-GboRow 'trust-probe-stores' 'unknown' ("$($first.Subject) appeared in $($first.Store) after $($first.Client) connected to $($first.Target), and without the pinned lists the run cannot tell whether it is an incident certificate; " + (Get-GboRemovalText $added $first.Thumbprint) + "; $context")
        return
    }
    $incident = @($added | Where-Object { $Watch.Digests.ContainsKey($_.Sha256) })
    if ($incident.Count -gt 0) {
        $first = $incident[0]
        $how = 'no event recorded it, so another program or a policy may have added it'
        if (@($incident | Where-Object { $_.Thumbprint -ceq $first.Thumbprint -and $_.LoggedByWindows }).Count -gt 0) { $how = "Windows' automatic update logged adding it" }
        Add-GboRow 'trust-probe-stores' 'at risk' ("$($Watch.Digests[$first.Sha256]) appeared in $($first.Store) after $($first.Client) connected to $($first.Target); $how; a program that reads this store, as Python 3.13 and newer do, now trusts its chains; " + (Get-GboRemovalText $incident $first.Thumbprint) + "; $context")
        return
    }
    $roots = @($added | Where-Object { $_.Store -like '*\Root' -or $_.Store -like '*\AuthRoot' } | Where-Object { -not $_.LoggedByWindows })
    if ($roots.Count -gt 0) {
        $first = $roots[0]
        Add-GboRow 'trust-probe-stores' 'question' ("a root that is not an incident certificate appeared in $($first.Store) after $($first.Client) connected to $($first.Target), and Windows did not log adding it: $($first.Subject); find out what added it; $context")
        return
    }
    $others = @($added | ForEach-Object { $_.Thumbprint } | Sort-Object -Unique).Count
    Add-GboRow 'trust-probe-stores' 'ok' ("no incident certificate appeared in the six certificate stores during the run; $others other certificate(s) appeared; " + $context)
}

# Invoke-GboNetworkChecks: bash's check --network: the offline checks, then the probe,
# with the store snapshot first, before any connection. The probe folder is removed at
# the end, whatever happened. A run whose store watch cannot start probes nothing.
function Invoke-GboNetworkChecks {
    $folder = New-GboProbeFolder
    try {
        $watch = $null
        $problem = ''
        try { $watch = Start-GboNetworkRun $folder } catch { $problem = $_.Exception.Message }
        Invoke-GboRunChecks
        if ($null -eq $watch) {
            Add-GboRow 'trust-probe-stores' 'unknown' "internal error: the store watch could not start, so nothing was probed: $problem"
            return
        }
        [void][IO.Directory]::CreateDirectory((Join-Path $folder 'dl'))
        Invoke-GboCheck 'trust-probe' { Invoke-GboProbeOwnLists $watch }
        Invoke-GboCheck 'trust-probe' { Invoke-GboProbeWindowsTrust $watch }
        Invoke-GboCheck 'trust-probe-downloads' { Add-GboDownloadsRow $folder }
        Invoke-GboCheck 'trust-probe-stores' { Add-GboStoresRow $watch }
        Invoke-GboCheck 'windows-update' { Invoke-GboCheckWindowsUpdate $folder }
    } finally {
        Remove-GboProbeFolder $folder
    }
}

# ---- the network run: Windows Update (bash: check_softwareupdate) ----------------------
# The search for updates not yet installed, run in a child Windows PowerShell from a
# file in the probe folder, so a search that hangs is killed with its process tree.
# Microsoft documents IUpdateSearcher.Search as "a synchronous search for updates"; it
# installs nothing, and it may contact Windows Update or the organisation's WSUS.
$GboUpdateSearch = @'
$ErrorActionPreference = 'Stop'
try {
    $searcher = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
    $result = $searcher.Search('IsInstalled=0 and IsHidden=0')
    'count=' + $result.Updates.Count
    foreach ($update in $result.Updates) { 'title=' + $update.Title }
} catch {
    'error=' + $_.Exception.Message
}
'@

# Invoke-GboCheckWindowsUpdate FOLDER [TIMEOUT]: the windows-update row (Windows only):
# the titles of the updates Windows Update lists as not installed, cut to 400
# characters as bash cuts softwareupdate's. It informs only.
function Invoke-GboCheckWindowsUpdate([string]$Folder, [int]$TimeoutSeconds = 180) {
    $windows = Get-GboSystemProgram 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not $windows) { Add-GboRow 'windows-update' 'unknown' 'no Windows PowerShell to run the Windows Update search in'; return }
    $file = Join-Path $Folder 'update-search.ps1'
    [IO.File]::WriteAllText($file, $GboUpdateSearch)
    $arguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $file)
    $answer = Invoke-GboNative -Path $windows -ArgumentList $arguments -TimeoutSeconds $TimeoutSeconds -WorkingDirectory $Folder
    if ($answer.TimedOut) { Add-GboRow 'windows-update' 'unknown' "Windows Update did not answer within $TimeoutSeconds s"; return }
    $lines = @($answer.StdOut -split "`n" | ForEach-Object { $_.Trim() })
    $failed = @($lines | Where-Object { $_.StartsWith('error=') })
    if ($failed.Count -gt 0) { Add-GboRow 'windows-update' 'unknown' ('Windows Update could not search: ' + $failed[0].Substring(6)); return }
    $count = @($lines | Where-Object { $_ -match '^count=\d+$' })
    if ($count.Count -eq 0) { Add-GboRow 'windows-update' 'unknown' 'the Windows Update search gave no answer'; return }
    $titles = @($lines | Where-Object { $_.StartsWith('title=') } | ForEach-Object { $_.Substring(6) })
    $evidence = 'nothing listed'
    if ($titles.Count -gt 0) { $evidence = "$($titles.Count) update(s) not installed: " + ($titles -join '; ') }
    if ($evidence.Length -gt 400) { $evidence = $evidence.Substring(0, 400) }
    Add-GboRow 'windows-update' 'info' $evidence
}

# ---- commands ---------------------------------------------------------------------
# bash: run_checks, in bash's order. Each Windows row sits where its bash counterpart
# does, and the rows only Windows has (wsl, edge, defender) beside their nearest
# one. Every check runs on its own, so one that fails becomes an 'unknown' row
# marked internal error, and the others still run.
function Invoke-GboRunChecks {
    Invoke-GboCheck 'windows' { Invoke-GboCheckWindows }
    Invoke-GboCheck 'wsl' { Invoke-GboCheckWsl }
    Invoke-GboCheck 'node' { Invoke-GboCheckNode }
    Invoke-GboCheck 'brotli' { Invoke-GboCheckBrotli }
    Invoke-GboCheck 'dotnet' { Invoke-GboCheckDotNet }
    Invoke-GboCheck 'pwsh' { Invoke-GboCheckPwsh }
    Invoke-GboCheck 'bun' { Invoke-GboCheckBun }
    Invoke-GboCheck 'claude' { Invoke-GboCheckClaude }
    Invoke-GboCheck 'tailscale' { Invoke-GboCheckTailscale }
    Invoke-GboCheck 'chrome' { Invoke-GboCheckChrome }
    Invoke-GboCheck 'edge' { Invoke-GboCheckEdge }
    Invoke-GboCheck 'pip' { Invoke-GboCheckPythonTools }
    Invoke-GboCheck 'cert-store' { Invoke-GboCheckCertStore }
    Invoke-GboCheck 'cert-store-deny' { Invoke-GboCheckCertStoreDeny }
    Invoke-GboCheck 'php' { Invoke-GboCheckPhp }
    Invoke-GboCheck 'gitlab' { Invoke-GboCheckGitlab }
    Invoke-GboCheck 'ruby' { Invoke-GboCheckRuby }
    Invoke-GboCheck 'nextjs' { Invoke-GboCheckPackages }
    Invoke-GboCheck 'pillow' { Invoke-GboCheckPillow }
    Invoke-GboCheck 'keys-platforms' { Invoke-GboCheckKeysPlatforms }
    Invoke-GboCheck 'firewall' { Invoke-GboCheckFirewall }
    Invoke-GboCheck 'defender' { Invoke-GboCheckDefender }
    Invoke-GboCheck 'npm-tokens' { Invoke-GboCheckNpmTokens }
    Invoke-GboCheck 'env-perms' { Invoke-GboCheckEnvPerms }
    Invoke-GboCheck 'listeners' { Invoke-GboCheckListeners }
    Invoke-GboCheck 'accounts' { Invoke-GboCheckAccounts }
}

# check [-Network] [-Json]: every read-only check, as a table or as JSON (bash:
# cmd_check, whose spellings --network and --json work too). Plain check writes no
# file, folder, setting or store. -Network first says on stderr what it will do.
function Invoke-GboCmdCheck([string[]]$Options) {
    $asJson = $false
    $network = $false
    foreach ($option in @($Options)) {
        if (@('-Json', '--json') -contains $option) { $asJson = $true; continue }
        if (@('-Network', '--network') -contains $option) { $network = $true; continue }
        warn "check: unknown option $option"
        return 2
    }
    $GboRows.Clear()
    $GboPinnedCache.Clear()
    if ($network) {
        Write-GboNetworkNotice
        Invoke-GboNetworkChecks
    } else {
        Invoke-GboRunChecks
    }
    Write-GboRows $asJson $network
    return 0
}

# Write-GboNetworkNotice: what check -Network does, said before it does it (the upstream
# Windows review's seventh point: say plainly that the network run makes a folder).
function Write-GboNetworkNotice {
    warn 'check -Network: each HTTPS client on this PC (curl, Python, pip, node, uv, Java, PowerShell and git)'
    warn 'connects to the incident page and its 14 test sites, one connection at a time.'
    warn 'It works in one new folder under TEMP and removes it at the end.'
    warn 'Windows may cache certificates and download data while it runs, and the Windows Update search'
    warn 'contacts Windows Update or WSUS. Six certificate stores are read before the first connection and'
    warn 'after every one; when an incident certificate appears, every later connection stops.'
}

function Write-GboUsage {
    say 'grip-black-october.ps1 -- check one Windows machine against the October 2026'
    say '("Black October") security floors, then apply each fix only after a typed yes.'
    say ''
    say 'Run it from a console window:'
    say '  powershell -NoProfile -ExecutionPolicy Bypass -File grip-black-october.ps1 <command>'
    say ''
    say '  check [-Network] [-Json]  every read-only check (the default); -Json prints JSON;'
    say '                            -Network also probes each HTTPS client against the'
    say '                            incident''s 14 test sites, and says what it does first'
    say '  help                      this text'
    say '  version                   the version of this script'
}

# Invoke-GboMain ARGV: the first word is the command (check when there is none, as in
# bash), and only check takes more words: its options. Returns the exit code: 0
# done, 2 bad usage.
function Invoke-GboMain {
    param([object[]]$Argv = @())
    $words = @($Argv | ForEach-Object { [string]$_ })
    $command = 'check'
    if ($words.Count -gt 0) { $command = $words[0] }
    $rest = @($words | Select-Object -Skip 1)
    if ($command -ceq 'check') { return (Invoke-GboCmdCheck $rest) }
    if ($rest.Count -gt 0) {
        warn ('unexpected argument(s): ' + ($rest -join ' '))
        Write-GboUsage
        return 2
    }
    if (@('help', '--help', '-h', '/?') -ccontains $command) { Write-GboUsage; return 0 }
    if (@('version', '--version') -ccontains $command) { say "grip-black-october.ps1 $GboVersion"; return 0 }
    warn "unknown command '$command'"
    Write-GboUsage
    return 2
}

# GBO_NO_MAIN=1 lets a test dot-source the functions without running a command.
if ($env:GBO_NO_MAIN -ne '1') { exit (Invoke-GboMain -Argv $args) }
