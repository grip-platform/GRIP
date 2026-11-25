<#
.SYNOPSIS
GRIP Telegram Relay — one-paste Windows setup.

.DESCRIPTION
Turns this Windows machine into an always-on GRIP host you can query from
the Telegram app on your phone, from anywhere. It:

  1. installs prerequisites (git, Python, Claude Code) via winget
  2. walks you through the one-time Claude Code login (your account)
  3. clones GRIP to %USERPROFILE%\.grip (browser sign-in to GitHub, once)
  4. asks for your bot token (from @BotFather) and validates it
  5. writes the relay config with a one-time ENROLL CODE (security: the
     relay answers nobody until YOU send that code from your phone)
  6. registers + starts a Scheduled Task so the relay survives reboots

Run it in a NORMAL PowerShell window (not as Administrator):
  powershell -ExecutionPolicy Bypass -File .\windows-telegram-relay-setup.ps1

Security: the bot token is stored ONLY in %USERPROFILE%\.grip\state\
telegram-relay\config.json (a gitignored path — it can never be committed).
The relay's spawned Claude is capability-locked to read-only tools
(Read,Grep,Glob) so message content can never write files or run commands
on this machine. See docs/windows-telegram-relay.md.
#>
[CmdletBinding()]
param(
    [string]$GripRepo   = "https://github.com/grip-platform/GRIP.git",
    [string]$GripBranch = "main",
    [string]$GripDir    = "$env:USERPROFILE\.grip",
    [string]$TaskName   = "GRIP Telegram Relay"
)

$ErrorActionPreference = "Stop"
function Info($m) { Write-Host "[grip] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[ ok ] $m" -ForegroundColor Green }
function Fail($m) { Write-Host "[fail] $m" -ForegroundColor Red; exit 1 }

function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Confirm-Tool([string]$Name, [string]$WingetId) {
    if (Get-Command $Name -ErrorAction SilentlyContinue) { Ok "$Name present"; return }
    Info "Installing $Name via winget ($WingetId)..."
    winget install -e --id $WingetId --accept-source-agreements --accept-package-agreements
    Update-SessionPath
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Fail "$Name is installed but not on PATH yet. Open a NEW PowerShell window and re-run this script."
    }
    Ok "$Name installed"
}

# ---- 0. winget --------------------------------------------------------------
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Fail "winget is missing. Install 'App Installer' from the Microsoft Store, then re-run."
}

# ---- 1. git + python --------------------------------------------------------
Confirm-Tool git    Git.Git
Confirm-Tool python Python.Python.3.12

# ---- 2. Claude Code ---------------------------------------------------------
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Info "Installing Claude Code (official installer)..."
    try {
        Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
    } catch {
        Info "Official installer unavailable — falling back to npm."
        Confirm-Tool node OpenJS.NodeJS.LTS
        npm install -g @anthropic-ai/claude-code
    }
    Update-SessionPath
}
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Fail "claude CLI not found after install. Open a NEW PowerShell window and re-run."
}
Ok "Claude Code present"

# ---- 3. One-time Claude login (must be you; browser flow) --------------------
$creds = "$env:USERPROFILE\.claude\.credentials.json"
if (-not (Test-Path $creds)) {
    Write-Host ""
    Info "Claude Code needs a ONE-TIME login on this machine."
    Info "An interactive Claude window opens now: complete /login in the browser,"
    Info "then type /exit — this setup continues automatically."
    Write-Host ""
    & claude
}

# ---- 4. GRIP ----------------------------------------------------------------
if (-not (Test-Path "$GripDir\.git")) {
    Info "Cloning GRIP -> $GripDir (a browser window may open for GitHub sign-in)..."
    git clone --branch $GripBranch $GripRepo $GripDir
    if ($LASTEXITCODE -ne 0) { Fail "git clone failed — check GitHub access to $GripRepo." }
} else {
    Info "GRIP already at $GripDir — updating branch '$GripBranch'..."
    git -C $GripDir fetch origin $GripBranch
    git -C $GripDir checkout $GripBranch
    git -C $GripDir pull --rebase origin $GripBranch
}
$relayPy = "$GripDir\lib\telegram_relay.py"
if (-not (Test-Path $relayPy)) {
    Fail "lib\telegram_relay.py not found — is '$GripBranch' the right branch?"
}
Ok "GRIP ready at $GripDir"

# ---- 5. Bot token -----------------------------------------------------------
Write-Host ""
Info "If you haven't made your bot yet: in Telegram, message @BotFather,"
Info "send /newbot, pick a name, and copy the token it gives you."
$token = (Read-Host "Paste the bot token from @BotFather").Trim()
try {
    $me = Invoke-RestMethod "https://api.telegram.org/bot$token/getMe"
    if (-not $me.ok) { Fail "Telegram rejected that token." }
} catch { Fail "Telegram rejected that token (typo, or no network)." }
$botUser = $me.result.username
Ok "Token valid — your bot is @$botUser"

# ---- 6. Config + one-time enroll code ----------------------------------------
$enroll   = Get-Random -Minimum 100000 -Maximum 999999
$stateDir = "$GripDir\state\telegram-relay"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$cfg = [ordered]@{
    bot_token        = $token
    allowed_user_ids = @()
    enroll_code      = "$enroll"
    grip_dir         = $GripDir
    model            = "claude-haiku-4-5-20251001"
    tools            = ""            # empty = read-only lock (Read,Grep,Glob)
    spawn_timeout_s  = 300
}
$cfg | ConvertTo-Json | Set-Content -Path "$stateDir\config.json" -Encoding UTF8
Ok "Config written -> $stateDir\config.json (gitignored — token stays on this machine)"

# ---- 7. Scheduled task: start at logon, restart on crash, never time-boxed ---
$pyDir = Split-Path (Get-Command python).Source
$pyw   = Join-Path $pyDir "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = (Get-Command python).Source }
$action   = New-ScheduledTaskAction -Execute $pyw -Argument "`"$relayPy`"" -WorkingDirectory $GripDir
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
                       -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$state = (Get-ScheduledTask -TaskName $TaskName).State
Ok "Scheduled task '$TaskName' registered and started (state: $state)"

# ---- 8. Done — two steps on the phone ----------------------------------------
Write-Host ""
Write-Host "==================  DONE — 2 steps left, ON YOUR PHONE  ==================" -ForegroundColor Yellow
Write-Host "  1. Open Telegram -> @$botUser -> send exactly:   $enroll"
Write-Host "     (that enrolls YOUR account; the relay ignores everyone else, forever)"
Write-Host "  2. Send:  /ping   -> expect 'GRIP relay alive on ...'"
Write-Host "     Then just ask GRIP anything. /fresh <question> starts a new session."
Write-Host ""
Write-Host "  Ops:  status ->  python `"$relayPy`" --status"
Write-Host "        log    ->  $stateDir\relay-log.jsonl"
Write-Host "        task   ->  Task Scheduler -> '$TaskName'"
Write-Host "  Keep this machine powered on and this user logged in for anywhere-access."
Write-Host "===========================================================================" -ForegroundColor Yellow
