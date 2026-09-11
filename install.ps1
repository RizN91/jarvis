#Requires -Version 5.1
<#
.SYNOPSIS
    One-command installer for Jarvis - the Windows dictation + GPT-Live voice
    assistant.

.DESCRIPTION
    Paste ONE line into PowerShell and you are done:

        irm https://raw.githubusercontent.com/RizN91/jarvis/main/install.ps1 | iex

    The script then:
      1. finds a 64-bit Python 3.11 or newer on this machine
      2. clones (or downloads) Jarvis into a per-user folder
      3. builds its private virtual environment and installs the pinned
         dependencies, by calling the project's own setup.cmd
      4. adds a Start Menu shortcut that starts the tray app

    It is a per-user install. It never elevates, never installs Python for you,
    never changes the PowerShell execution policy, never changes a Defender
    setting, and never modifies your global Python.

    You still supply your own OpenAI API key in the setup wizard on first
    launch; the key is stored in Windows Credential Manager, never in a file.

.PARAMETER Dir
    Where to install Jarvis. Default: %LOCALAPPDATA%\Jarvis\app

.PARAMETER Version
    Git ref (branch or tag) to install. Default: main

.PARAMETER Desktop
    Also create a Desktop shortcut.

.PARAMETER DryRun
    Print exactly what would happen and change nothing at all. Exits 0.

.PARAMETER Uninstall
    Remove Jarvis. Combine with -DryRun to list what would be removed without
    deleting anything. The data folder and the stored API key are only removed
    after an explicit typed confirmation, and are never touched otherwise.

.EXAMPLE
    irm https://raw.githubusercontent.com/RizN91/jarvis/main/install.ps1 | iex

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -DryRun

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -Uninstall -DryRun
#>
[CmdletBinding()]
param(
    [string]$Dir,
    [string]$Version = 'main',
    [switch]$Desktop,
    [switch]$DryRun,
    [switch]$Uninstall
)

$RepoUrl  = 'https://github.com/RizN91/jarvis.git'
$RepoSlug = 'RizN91/jarvis'
$MainRef  = 'main'

# ---------------------------------------------------------------- output helpers

function Show-Step  { param([string]$Message) Write-Host ""; Write-Host "==> $Message" -ForegroundColor Cyan }
function Show-Ok    { param([string]$Message) Write-Host "    [ok]  $Message" -ForegroundColor Green }
function Show-Info  { param([string]$Message) Write-Host "    $Message" }
function Show-Warn  { param([string]$Message) Write-Host "    [!]   $Message" -ForegroundColor Yellow }
function Show-Fail  { param([string]$Message) Write-Host "    [x]   $Message" -ForegroundColor Red }

# ---------------------------------------------------------------------- location

if (-not $env:LOCALAPPDATA) {
    Show-Fail 'LOCALAPPDATA is not set - this is not a normal Windows user session.'
    exit 1
}

$DataDir = Join-Path $env:LOCALAPPDATA 'Jarvis'
if ($Dir) {
    $AppDir = [System.IO.Path]::GetFullPath($Dir)
} else {
    $AppDir = Join-Path $DataDir 'app'
}
$VenvDir = Join-Path $AppDir '.venv'
$VenvPy  = Join-Path $VenvDir 'Scripts\python.exe'
$VenvPyw = Join-Path $VenvDir 'Scripts\pythonw.exe'

$ProgramsDir  = [Environment]::GetFolderPath('Programs')
$StartMenuLnk = Join-Path $ProgramsDir 'Jarvis.lnk'
$DesktopLnk   = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Jarvis.lnk'
$StartupLnk   = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Jarvis.lnk'

# ---------------------------------------------------------------- python detection

function Get-SuitablePython {
    # The lockfile (requirements.lock.txt) was frozen on CPython 3.11, so an
    # exact 3.11 is preferred wherever it is found; otherwise the first
    # suitable interpreter in the documented order is used.
    $probe = 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) and sys.maxsize > 2**32 else 1)'
    $candidates = @(
        @{ Exe = 'py';     Args = @('-3.11'); Label = 'py -3.11' },
        @{ Exe = 'py';     Args = @('-3.12'); Label = 'py -3.12' },
        @{ Exe = 'py';     Args = @('-3');    Label = 'py -3'    },
        @{ Exe = 'python'; Args = @();        Label = 'python'   }
    )

    $suitable = @()
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        $pre = @($candidate.Args)
        try {
            $null = & $candidate.Exe @pre -c $probe 2>$null
            if ($LASTEXITCODE -ne 0) { continue }
            $exePath = (& $candidate.Exe @pre -c 'import sys; print(sys.executable)' 2>$null | Select-Object -First 1)
            $version = (& $candidate.Exe @pre -c 'import sys; print(sys.version.split()[0])' 2>$null | Select-Object -First 1)
            if (-not $exePath) { continue }
            $suitable += [pscustomobject]@{
                Label   = $candidate.Label
                Exe     = ("$exePath").Trim()
                Version = ("$version").Trim()
            }
        } catch {
            # Not a usable interpreter - try the next candidate.
        }
    }

    if ($suitable.Count -eq 0) { return $null }
    $exact311 = $suitable | Where-Object { $_.Version -like '3.11.*' } | Select-Object -First 1
    if ($exact311) { return $exact311 }
    return $suitable[0]
}

# ---------------------------------------------------------------- small utilities

function Show-Banner {
    Write-Host ""
    Write-Host "  ======================================================================" -ForegroundColor DarkCyan
    if ($Uninstall) {
        Write-Host "   Jarvis uninstaller" -ForegroundColor White
    } else {
        Write-Host "   Jarvis installer" -ForegroundColor White
    }
    Write-Host "  ======================================================================" -ForegroundColor DarkCyan
    Write-Host ""

    if ($Uninstall) {
        Write-Host "   This removes:"
        Write-Host "     - the Jarvis application and its .venv at:"
        Write-Host "         $AppDir"
        Write-Host "     - the Start Menu / Desktop shortcuts it created"
        Write-Host ""
        Write-Host "   Your data folder and stored API key are listed too, but they are"
        Write-Host "   ONLY removed after you type an explicit confirmation - never"
        Write-Host "   silently, and never by a dry run."
        Write-Host ""
    } else {
        Write-Host "   This will:"
        Write-Host "     1. find a 64-bit Python 3.11 or newer on this machine"
        Write-Host "     2. put Jarvis at:"
        Write-Host "          $AppDir"
        Write-Host "     3. build its private virtual environment there (.venv)"
        Write-Host "     4. install the pinned dependencies into that .venv"
        Write-Host "     5. add a Start Menu shortcut that starts the tray app"
        if ($Desktop) { Write-Host "        and a Desktop shortcut (-Desktop was given)" }
        Write-Host ""
        Write-Host "   It will NOT:"
        Write-Host "     - install Python for you (it tells you where to get it)"
        Write-Host "     - ask for or use administrator rights"
        Write-Host "     - change the PowerShell execution policy"
        Write-Host "     - change any Windows Defender setting"
        Write-Host "     - modify your global Python installation"
        Write-Host ""
        Write-Host "   Your OpenAI API key is NOT included. The first launch opens a setup"
        Write-Host "   wizard; the key is stored in Windows Credential Manager, never in a"
        Write-Host "   file, and nothing is sent anywhere until you finish the wizard."
        Write-Host ""
    }

    if ($DryRun) {
        Write-Host "   DRY RUN: nothing will be created, downloaded or changed." -ForegroundColor Yellow
        Write-Host ""
    }
}

function Copy-TreeContents {
    param([string]$From, [string]$To)
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    foreach ($item in (Get-ChildItem -LiteralPath $From -Force)) {
        Copy-Item -LiteralPath $item.FullName -Destination $To -Recurse -Force
    }
}

function New-JarvisShortcut {
    param([string]$LinkPath, [string]$AppDir, [string]$PythonwPath)
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($LinkPath)
    $link.TargetPath = $PythonwPath
    $link.Arguments = '-m jarvis'
    $link.WorkingDirectory = $AppDir
    $link.Description = 'Jarvis - dictation and voice assistant'
    $link.Save()
}

function Invoke-Install {
    # ---- 1. find a Python ------------------------------------------------
    Show-Step 'Looking for a 64-bit Python 3.11 or newer'
    $python = Get-SuitablePython
    if (-not $python) {
        Show-Fail 'No suitable interpreter was found.'
        Write-Host "    Checked, in order: py -3.11, py -3.12, py -3, python" -ForegroundColor Gray
        Write-Host ""
        Write-Host "    Install 64-bit Python 3.11 from:" -ForegroundColor Yellow
        Write-Host "        https://www.python.org/downloads/" -ForegroundColor White
        Write-Host "    and tick 'Add python.exe to PATH' in its installer, then re-run this."
        Write-Host "    This installer will not download or install Python for you."
        Write-Host ""
        return 1
    }
    Show-Ok "using $($python.Label)  ->  Python $($python.Version)"
    Show-Info "interpreter: $($python.Exe)"

    # ---- 2. clone or download the source ---------------------------------
    $existing  = Test-Path -LiteralPath $AppDir
    $isGitTree = Test-Path -LiteralPath (Join-Path $AppDir '.git')
    $haveGit   = [bool](Get-Command git -ErrorAction SilentlyContinue)
    $method    = ''

    if ($isGitTree) {
        Show-Step "Updating the existing checkout at $AppDir (git pull, not a re-clone)"
        if ($DryRun) {
            Show-Info "would run: git -C `"$AppDir`" fetch --tags"
            Show-Info "would run: git -C `"$AppDir`" checkout $Version"
            Show-Info "would run: git -C `"$AppDir`" pull --ff-only"
            $method = 'git pull on the existing checkout'
        } else {
            & git -C $AppDir fetch --tags --quiet
            & git -C $AppDir checkout --quiet $Version
            & git -C $AppDir pull --ff-only --quiet
            if ($LASTEXITCODE -ne 0) {
                Show-Fail "git could not update $AppDir."
                Show-Info "Re-run with -Dir <other path> for a clean install, or remove that folder yourself."
                return 1
            }
            Show-Ok "updated the existing checkout with git pull"
            $method = 'git pull on the existing checkout'
        }
    } else {
        $staging = Join-Path ([System.IO.Path]::GetTempPath()) ("jarvis-install-" + [Guid]::NewGuid().ToString('N'))
        if ($haveGit) {
            # If the target already exists as a non-git folder (a previous zip
            # install), clone into staging and copy over it; git clone refuses
            # to write into a non-empty directory.
            $target = if ($existing) { Join-Path $staging 'src' } else { $AppDir }
            Show-Step "Cloning Jarvis ($Version) with git"
            if ($DryRun) {
                Show-Info "would run: git clone --depth 1 --branch $Version $RepoUrl `"$target`""
                if ($existing) { Show-Info "would refresh the existing folder at $AppDir from that clone" }
            } else {
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
                & git clone --depth 1 --branch $Version $RepoUrl $target
                if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $target 'setup.cmd'))) {
                    Show-Fail 'git clone failed (no network, or the ref does not exist).'
                    return 1
                }
                if ($existing) {
                    Copy-TreeContents -From $target -To $AppDir
                    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
                }
                Show-Ok "cloned with git ($Version)"
            }
            $method = 'git clone'
        } else {
            $zipUrl = if ($Version -eq $MainRef) {
                "https://github.com/$RepoSlug/archive/refs/heads/$MainRef.zip"
            } else {
                "https://github.com/$RepoSlug/archive/refs/tags/$Version.zip"
            }
            Show-Step "git is not installed - downloading the release zip instead"
            if ($DryRun) {
                Show-Info "would download: $zipUrl"
                Show-Info "would unpack it into: $AppDir"
            } else {
                New-Item -ItemType Directory -Force -Path $staging | Out-Null
                $zipPath = Join-Path $staging 'jarvis.zip'
                try {
                    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
                    Expand-Archive -LiteralPath $zipPath -DestinationPath $staging -Force
                } catch {
                    Show-Fail "download or unpack failed: $($_.Exception.Message)"
                    Show-Info "URL: $zipUrl"
                    return 1
                }
                $root = Get-ChildItem -LiteralPath $staging -Directory | Select-Object -First 1
                if (-not $root -or -not (Test-Path (Join-Path $root.FullName 'setup.cmd'))) {
                    Show-Fail 'the downloaded archive did not contain the Jarvis source tree.'
                    return 1
                }
                Copy-TreeContents -From $root.FullName -To $AppDir
                Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
                Show-Ok "downloaded and unpacked the release zip"
            }
            $method = 'release zip download (git was not found)'
        }
    }

    if (-not $DryRun) { Show-Info "installed using: $method" }

    # ---- 3. venv + dependencies, via the project's own setup.cmd ---------
    $setup = Join-Path $AppDir 'setup.cmd'
    Show-Step 'Creating the virtual environment and installing dependencies (setup.cmd)'
    if ($DryRun) {
        Show-Info "would run: `"$setup`""
        Show-Info "  -> it creates $VenvDir and installs requirements.lock.txt into it"
    } else {
        if (-not (Test-Path -LiteralPath $setup)) {
            Show-Fail "setup.cmd is missing from $AppDir - the download looks incomplete."
            return 1
        }
        # Hand the interpreter we already validated to setup.cmd so both halves
        # of the installer agree on which Python to build the venv from.
        $env:JARVIS_PYTHON_BASE = $python.Exe
        Push-Location $AppDir
        try {
            & cmd.exe /c $setup
        } finally {
            Pop-Location
            Remove-Item Env:\JARVIS_PYTHON_BASE -ErrorAction SilentlyContinue
        }
        if ($LASTEXITCODE -ne 0) {
            Show-Fail 'setup.cmd failed - read its output above; it says what to do.'
            return 1
        }
        Show-Ok 'setup.cmd finished: dependencies installed and import-checked'
    }

    # ---- 4. shortcut -----------------------------------------------------
    Show-Step 'Creating the Start Menu shortcut'
    if ($DryRun) {
        Show-Info "would create: $StartMenuLnk"
        Show-Info "  -> target: $VenvPyw -m jarvis   (working dir: $AppDir)"
        if ($Desktop) { Show-Info "would create: $DesktopLnk" }
    } else {
        if (-not (Test-Path -LiteralPath $VenvPyw)) {
            Show-Warn "pythonw.exe is not at $VenvPyw - skipping the shortcut."
        } else {
            try {
                New-JarvisShortcut -LinkPath $StartMenuLnk -AppDir $AppDir -PythonwPath $VenvPyw
                Show-Ok "Start Menu: $StartMenuLnk"
                if ($Desktop) {
                    New-JarvisShortcut -LinkPath $DesktopLnk -AppDir $AppDir -PythonwPath $VenvPyw
                    Show-Ok "Desktop:    $DesktopLnk"
                }
            } catch {
                Show-Warn "could not create a shortcut: $($_.Exception.Message)"
                Show-Info "you can still launch it from $AppDir\run.cmd"
            }
        }
    }

    # ---- 5. what is next -------------------------------------------------
    if ($DryRun) {
        Show-Step 'Dry run complete - nothing was changed'
    } else {
        Show-Step 'Jarvis is installed'
    }
    if (-not $DryRun) {
        Show-Info "location:    $AppDir"
        Show-Info "interpreter: $VenvPy"
        Write-Host ""
        Write-Host "  NEXT STEP (required before it can do anything):" -ForegroundColor Yellow
        Write-Host "    Launch Jarvis from the Start Menu. The first launch opens the"
        Write-Host "    setup wizard, which asks for YOUR OpenAI API key and walks"
        Write-Host "    through microphone, speakers, hotkeys and budget. The key goes"
        Write-Host "    into Windows Credential Manager - never into a file."
        Write-Host ""
        Write-Host "  Still to do / not done for you:" -ForegroundColor Gray
        Write-Host "    - the wizard itself (it is interactive)"
        Write-Host "    - WebView2: already present on Windows 11; on Windows 10 see"
        Write-Host "      https://developer.microsoft.com/microsoft-edge/webview2/"
        Write-Host "    - an OpenAI API key with credit"
        Write-Host ""
        Write-Host "  To launch from a shell:" -ForegroundColor Gray
        Write-Host "    `"$VenvPy`" -m jarvis"
        Write-Host "  For a visible console when troubleshooting:" -ForegroundColor Gray
        Write-Host "    `"$VenvPy`" -m jarvis    OR   `"$AppDir\run-console.cmd`""
        Write-Host ""
        Write-Host "  To remove it later:" -ForegroundColor Gray
        Write-Host "    powershell -File install.ps1 -Uninstall -DryRun    # list, deletes nothing"
        Write-Host "    powershell -File install.ps1 -Uninstall            # actually remove"
        Write-Host ""
    }
    return 0
}

# -------------------------------------------------------------------- uninstall

function Get-RemovalPlan {
    return @(
        [pscustomobject]@{ Kind = 'app';  Path = $AppDir
                           Note = 'the Jarvis application and its .venv (re-running the installer recreates it)' }
        [pscustomobject]@{ Kind = 'link'; Path = $StartMenuLnk
                           Note = 'Start Menu shortcut' }
        [pscustomobject]@{ Kind = 'link'; Path = $DesktopLnk
                           Note = 'Desktop shortcut (only if one was created)' }
        [pscustomobject]@{ Kind = 'link'; Path = $StartupLnk
                           Note = 'start-at-login shortcut (only if you enabled it)' }
        [pscustomobject]@{ Kind = 'data'; Path = $DataDir
                           Note = 'YOUR DATA: config.json, jarvis.db (history + spend), logs, wake-word models' }
        [pscustomobject]@{ Kind = 'cred'; Path = 'Jarvis/openai_api_key'
                           Note = 'Windows Credential Manager entry holding your API key reference' }
    )
}

function Show-RemovalPlan {
    param([switch]$WouldRemove)
    $verb = if ($WouldRemove) { 'WOULD REMOVE' } else { 'WILL REMOVE' }
    foreach ($item in (Get-RemovalPlan)) {
        Write-Host ("  {0}  {1}" -f $verb, $item.Path) -ForegroundColor Yellow
        Write-Host ("      {0}" -f $item.Note)
    }
}

function Invoke-Uninstall {
    Show-Step 'Uninstall plan'
    Show-RemovalPlan -WouldRemove

    if ($DryRun) {
        Write-Host ""
        Write-Host "  DRY RUN - nothing was deleted. The data folder and the stored API" -ForegroundColor Green
        Write-Host "  key above are ONLY ever removed after you type an explicit" -ForegroundColor Green
        Write-Host "  confirmation, and are never touched by a dry run." -ForegroundColor Green
        Write-Host ""
        return 0
    }

    Write-Host ""
    Write-Host "  The data folder and the stored API key are NOT removed by the next" -ForegroundColor Yellow
    Write-Host "  step no matter what you type - they need their own confirmation." -ForegroundColor Yellow
    Write-Host ""
    $answer = Read-Host '  Type UNINSTALL (capitals) to remove the app + shortcuts, anything else to abort'
    if ($answer -cne 'UNINSTALL') {
        Write-Host ""
        Write-Host '  Aborted - nothing was deleted.' -ForegroundColor Gray
        return 0
    }

    foreach ($item in (Get-RemovalPlan)) {
        if ($item.Kind -eq 'data' -or $item.Kind -eq 'cred') { continue }
        if (-not (Test-Path -LiteralPath $item.Path)) {
            Show-Info "not present - skipped: $($item.Path)"
            continue
        }
        if ($item.Kind -eq 'link') {
            Remove-Item -LiteralPath $item.Path -Force -ErrorAction SilentlyContinue
        } elseif ($item.Kind -eq 'app') {
            Remove-Item -LiteralPath $item.Path -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $item.Path) {
            Show-Warn "could not remove: $($item.Path)"
        } else {
            Show-Ok "removed: $($item.Path)"
        }
    }

    Write-Host ""
    Write-Host "  Your data is still intact at:" -ForegroundColor Yellow
    Write-Host "      $DataDir"
    Write-Host "  It holds your history and the reference to your API key, so it is" -ForegroundColor Gray
    Write-Host "  only removed on an explicit request." -ForegroundColor Gray
    Write-Host ""
    $purge = Read-Host '  Type DELETE (capitals) to ALSO remove that data folder and the stored API key; anything else keeps them'
    if ($purge -cne 'DELETE') {
        Write-Host ""
        Write-Host '  Kept your data and your API key. Nothing else remains.' -ForegroundColor Gray
        return 0
    }

    if (Test-Path -LiteralPath $DataDir) {
        Remove-Item -LiteralPath $DataDir -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $DataDir) {
            Show-Warn "could not fully remove: $DataDir"
        } else {
            Show-Ok "removed: $DataDir"
        }
    } else {
        Show-Info "not present - skipped: $DataDir"
    }
    & cmdkey /delete:Jarvis/openai_api_key | Out-Null
    Show-Ok 'removed the Windows Credential Manager entry Jarvis/openai_api_key'

    Write-Host ""
    Write-Host '  Done. Your Python installation and the source folder were left alone.' -ForegroundColor Green
    Write-Host ''
    return 0
}

# ------------------------------------------------------------------------- main

Show-Banner

$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal -ArgumentList $identity
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Show-Warn 'running from an elevated shell - Jarvis is a per-user install and does not need it.'
}

try {
    if ($Uninstall) {
        $code = Invoke-Uninstall
    } else {
        $code = Invoke-Install
    }
} catch {
    Show-Fail "unexpected error: $($_.Exception.Message)"
    $code = 1
}

if ($DryRun) { exit 0 }
exit $code
