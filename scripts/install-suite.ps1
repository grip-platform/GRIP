<#
install-suite.ps1 -- install the GRIP Suite (GRIP, HAL, HAPPI and GRASP) on Windows.

    powershell -ExecutionPolicy Bypass -File install-suite.ps1 [-DryRun]
    irm <url-of-this-file> | iex

For a dry run through iex, set $env:GRIP_SUITE_DRY_RUN = '1' first.

It checks for Python 3.11 or newer and Git for Windows, and installs them with winget
when they are missing, per user first. On Windows for Arm the Python is the x64 build,
which Windows runs under emulation, because GRIP's core packages have no Windows-Arm
wheels. It clones GRIP into ~\Projects\grip, or
$env:GRIP_SUITE_DIR, and runs the suite installer from that clone:
python -m lib.suite_install. That installs GRIP, HAL, HAPPI and GRASP and proves each
one answers. Run it again and the steps that already hold are skipped.

GRIP's own installer and HAL's commands are bash scripts, so for this run:
- Git for Windows' bash comes first on PATH, ahead of the bash.exe that WSL puts in
  System32, which would run them inside Linux instead;
- python3 means the Python found, because the bash scripts call plain python3, and on
  Windows that name is often the Microsoft Store alias;
- every clone keeps Unix line endings (core.autocrlf=false), because a script checked
  out with Windows line endings does not run in bash, and HAL has no .gitattributes.

Exit codes are the suite installer's: 0 everything holds, 1 something failed, 2 a dry
run found work to do, 130 stopped with Ctrl-C.

Everything is inside functions and only the last lines call Install-GripSuite, so a
download cut short runs nothing. Native commands inside a function that returns a value
are piped to Out-Host, because their output would otherwise become part of that value.
#>
param([switch]$DryRun)

function Get-SuiteRepoUrl {
    if ($env:GRIP_REPO_URL) { return $env:GRIP_REPO_URL }
    return 'https://github.com/grip-platform/GRIP.git'
}

function Get-SuiteDir {
    if ($env:GRIP_SUITE_DIR) { return $env:GRIP_SUITE_DIR }
    return (Join-Path (Join-Path $HOME 'Projects') 'grip')
}

function Write-Problem([string]$Text) {
    [Console]::Error.WriteLine("GRIP Suite: $Text")
}

# Test-WindowsOnArm: true on Windows for Arm. GRIP's core packages have no Windows-Arm
# wheels (on PyPI, 2026-11-25, sqlite-vec 0.1.9 has none and no source package), so there
# the door uses the x64 Python, which Windows runs under its x64 emulation.
function Test-WindowsOnArm {
    if ($PSVersionTable.PSEdition -eq 'Core' -and -not $IsWindows) { return $false }
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { return $true }
    # An emulated x64 PowerShell reports AMD64 above, and .NET before 7 reports X64 below
    # (Codex on GRIP#9884), while the machine-wide value Windows keeps is the native one.
    if ([Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE', 'Machine') -eq 'ARM64') { return $true }
    try {
        return ([string][System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq 'Arm64')
    } catch {
        return $false
    }
}

# Get-WindowsBuild: the Windows build number. Build 22000 is Windows 11, the first Windows
# on Arm that runs x64 programs (Windows 10 on Arm emulates only x86 ones).
function Get-WindowsBuild {
    return [System.Environment]::OSVersion.Version.Build
}

# Test-PythonOk: true when the command runs Python 3.11 or newer and is the x64 build.
# GRIP's core packages have no 32-bit wheels any more than they have Windows-Arm ones
# (the reason Test-WindowsOnArm gives), so this is checked everywhere, not only on
# Windows for Arm: off Arm, a 32-bit install used to sail past a version-only check
# (GRIP#9885). The Microsoft Store alias (WindowsApps\python.exe) prints a message
# and fails, so it never passes.
function Test-PythonOk([string]$Exe, [string[]]$Prefix) {
    $check = "import sys, sysconfig; sys.exit(0 if sys.version_info >= (3, 11) and sysconfig.get_platform() == 'win-amd64' else 1)"
    try {
        & $Exe @Prefix -c $check 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# Get-InstalledPythons: the python.exe files the python.org installer leaves, per user and
# for everyone, newest version first: Python315, Python313-arm64 and so on. On Windows for
# Arm the x64 build is the folder with no suffix, while the py launcher may prefer the Arm
# build. Test-PythonOk decides which one counts.
function Get-InstalledPythons {
    $roots = @()
    if ($env:LOCALAPPDATA) { $roots += Join-Path $env:LOCALAPPDATA 'Programs\Python' }
    if ($env:ProgramFiles) { $roots += $env:ProgramFiles }
    # A 32-bit PowerShell sees Program Files (x86) above; the 64-bit folder is this one.
    if ($env:ProgramW6432) { $roots += $env:ProgramW6432 }
    $roots | Select-Object -Unique | Where-Object { Test-Path $_ } |
        ForEach-Object { Get-ChildItem -Path $_ -Directory -ErrorAction SilentlyContinue } |
        Where-Object { $_.Name -match '^Python3\d+(-arm64|-32)?$' } |
        Sort-Object { [int]($_.Name -replace '^Python3(\d+).*$', '$1') } -Descending |
        ForEach-Object { Join-Path $_.FullName 'python.exe' } |
        Where-Object { Test-Path $_ } |
        ForEach-Object { @{ Exe = $_; Prefix = @() } }
}

# Find-Python: the interpreter path of the first suitable Python: the py launcher, python
# and python3 on PATH, then the installer's own folders.
function Find-Python {
    $candidates = @(
        @{ Exe = 'py'; Prefix = @('-3') },
        @{ Exe = 'python'; Prefix = @() },
        @{ Exe = 'python3'; Prefix = @() }
    ) + @(Get-InstalledPythons)
    foreach ($candidate in $candidates) {
        $exe = $candidate.Exe
        $prefix = $candidate.Prefix
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        if (-not (Test-PythonOk $exe $prefix)) { continue }
        $path = & $exe @prefix -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $path) { return ([string]$path).Trim() }
    }
    return $null
}

# Get-GitBashDir: Git for Windows' bin folder, the one holding bash.exe, found from git.
function Get-GitBashDir {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) { return $null }
    $dir = Split-Path $git.Source -Parent
    foreach ($step in 1..3) {
        $dir = Split-Path $dir -Parent
        if (-not $dir) { return $null }
        if (Test-Path (Join-Path $dir 'bin\bash.exe')) { return (Join-Path $dir 'bin') }
    }
    return $null
}

# Join-PathList: the entries of $Current, then each entry of $More that is not there yet.
function Join-PathList([string]$Current, [string[]]$More) {
    $seen = @{}
    $entries = foreach ($entry in ((@($Current) + $More) -split ';')) {
        $clean = $entry.Trim()
        if ($clean -and -not $seen.ContainsKey($clean)) {
            $seen[$clean] = $true
            $clean
        }
    }
    return (@($entries) -join ';')
}

# Update-SessionPath: a program winget installs reaches the PATH Windows saves, not the
# PATH of an open window, and a window opened before an earlier run still lacks it. So
# this adds the saved entries the window lacks, and keeps the window's own entries first.
function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = Join-PathList $env:Path @($machine, $user)
}

function Install-WithWinget([string]$Id, [string[]]$Extra = @()) {
    Write-Host "Installing $Id with winget, per user first"
    winget install --id $Id --exact --scope user --silent --accept-package-agreements --accept-source-agreements @Extra | Out-Host
    if ($LASTEXITCODE -eq 0) { return $true }
    Write-Host "A per-user install of $Id did not work, so this tries the default (Windows may ask for permission)"
    winget install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements @Extra | Out-Host
    return ($LASTEXITCODE -eq 0)
}

# Get-WingetExtra: extra winget arguments for one package. Python is always the x64
# build -- off Arm because GRIP's core packages have no 32-bit wheels (Test-PythonOk's
# reason), on Arm because they have no Windows-Arm wheels either (Test-WindowsOnArm's
# reason) -- so this forces x64 for Python on every host, not only on Arm (Codex on
# GRIP#10108; the installer-repair step had stayed Arm-only after the version check
# became universal). --force is needed because winget treats a second build of an
# installed Python as an upgrade and installs nothing (measured in a Windows 11 Arm VM
# on 2026-11-25: "No available upgrade found", exit -1978335189; the same happens off
# Arm when the existing build is 32-bit). With --force, it installs beside the other
# build.
function Get-WingetExtra([string]$Id) {
    if ($Id -like 'Python.*') {
        if (Test-WindowsOnArm) {
            Write-Host "Windows on Arm: this installs the x64 Python, which Windows runs under emulation, because GRIP's core packages have no Windows-Arm wheels."
        }
        return ,@('--architecture', 'x64', '--force')
    }
    return ,@()
}

function Install-Prerequisites([bool]$NeedPython, [bool]$NeedGit) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        $python = 'Python 3.11+'
        if (Test-WindowsOnArm) { $python = 'the Windows x64 installer of Python 3.11+ (not the ARM64 one)' }
        Write-Problem "winget is missing, so Python and Git cannot be installed for you."
        Write-Problem "Install $python from https://www.python.org/downloads/ and Git from https://git-scm.com/download/win, then run this again."
        return $false
    }
    $ids = @()
    if ($NeedPython) { $ids += 'Python.Python.3.13' }
    if ($NeedGit) { $ids += 'Git.Git' }
    foreach ($id in $ids) {
        if (-not (Install-WithWinget $id (Get-WingetExtra $id))) {
            Write-Problem "winget could not install $id; the reason is above."
            return $false
        }
    }
    Update-SessionPath
    return $true
}

# Get-Grip: clone GRIP, or update the clone already there. A folder that is not a Git
# clone is never touched. Returns 0, 1 on a problem, or 2 for a dry run with no clone.
function Get-Grip([bool]$Dry) {
    $dir = Get-SuiteDir
    $url = Get-SuiteRepoUrl
    if (Test-Path (Join-Path $dir '.git')) {
        if (-not $Dry) {
            Write-Host "Updating GRIP in $dir"
            git -C $dir pull --ff-only -q | Out-Host
            if ($LASTEXITCODE -ne 0) { Write-Host "Could not update $dir, so this run uses it as it is." }
        }
    } elseif (Test-Path $dir) {
        Write-Problem "$dir exists and is not a Git clone of GRIP. Move it, or set GRIP_SUITE_DIR to another folder."
        return 1
    } elseif ($Dry) {
        Write-Host "Dry run: a real run would clone GRIP from $url into $dir."
        return 2
    } else {
        Write-Host "Cloning GRIP into $dir"
        New-Item -ItemType Directory -Force -Path (Split-Path $dir -Parent) | Out-Null
        git clone -q -c core.autocrlf=false $url $dir | Out-Host
        if ($LASTEXITCODE -ne 0) { return 1 }
    }
    if (-not (Test-Path (Join-Path (Join-Path (Join-Path $dir 'lib') 'suite_install') '__main__.py'))) {
        Write-Problem "$dir has no suite installer (lib\suite_install). Update it, or set GRIP_SUITE_DIR to a newer clone."
        return 1
    }
    return 0
}

# Set-SuiteEnvironment: Git for Windows' bash and tools first on PATH, python3 meaning
# the Python found, and Unix line endings for every clone in this run.
function Set-SuiteEnvironment([string]$Python, [string]$BashDir) {
    $usrBin = Join-Path (Split-Path $BashDir -Parent) 'usr\bin'
    $shim = Join-Path ([System.IO.Path]::GetTempPath()) ('grip-suite-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $shim | Out-Null
    $target = $Python.Replace('\', '/')
    [System.IO.File]::WriteAllText((Join-Path $shim 'python3'), "#!/bin/sh`nexec `"$target`" `"`$@`"`n")
    $env:Path = "$shim;$BashDir;$usrBin;$env:Path"
    $env:GIT_CONFIG_COUNT = '1'
    $env:GIT_CONFIG_KEY_0 = 'core.autocrlf'
    $env:GIT_CONFIG_VALUE_0 = 'false'
}

function Install-GripSuite([bool]$Dry) {
    # A window opened before an earlier run lacks what that run installed.
    Update-SessionPath
    if ((Test-WindowsOnArm) -and (Get-WindowsBuild) -lt 22000) {
        Write-Problem "Windows 10 on Arm cannot run x64 programs, and on Arm GRIP needs the x64 Python, because its core packages have no Windows-Arm wheels. Use Windows 11, or an x64 PC."
        return 1
    }
    $python = Find-Python
    $hasGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
    if (-not $python -or -not $hasGit) {
        if ($Dry) {
            Write-Problem "Dry run: Python 3.11+ or Git is missing, and a real run would install it."
            return 2
        }
        if (-not (Install-Prerequisites (-not $python) (-not $hasGit))) { return 1 }
        $python = Find-Python
        if (-not $python) {
            Write-Problem "Python 3.11 or newer is still not found. Open a new PowerShell window and run this again."
            return 1
        }
    }
    $bashDir = Get-GitBashDir
    if (-not $bashDir) {
        Write-Problem "Git for Windows' bash was not found beside git. Reinstall Git for Windows, then run this again."
        return 1
    }
    Write-Host "Using $(& $python --version 2>&1) at $python, and $(git --version)."
    $code = Get-Grip $Dry
    if ($code -ne 0) { return $code }
    Set-SuiteEnvironment $python $bashDir
    Push-Location (Get-SuiteDir)
    try {
        if ($Dry) {
            & $python -m lib.suite_install --dry-run | Out-Host
        } else {
            & $python -m lib.suite_install | Out-Host
        }
        return $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

if ($env:GRIP_SUITE_RUN_MAIN -ne '0') {
    $code = Install-GripSuite ($DryRun.IsPresent -or $env:GRIP_SUITE_DRY_RUN -eq '1')
    if ($PSCommandPath) { exit $code }
    $global:LASTEXITCODE = $code
}
