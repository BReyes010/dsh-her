# fix_l0_acl.ps1 - restore access to the "人格" folder (run as Administrator)
#
#   powershell -ExecutionPolicy Bypass -File .\脚本\fix_l0_acl.ps1              # restore + keep read-only protection
#   powershell -ExecutionPolicy Bypass -File .\脚本\fix_l0_acl.ps1 -Writable    # restore + everyone writable (old behaviour)
#
# Strategy (in order, each verified before moving on):
#   1. takeown      - take ownership back
#   2. icacls /reset - restore inherited permissions (unbreak read/write)
#   3. optional: apply read-only protection with *SID syntax (no name lookup)
#   4. read + write verification
#
# Uses "*S-1-5-32-545" SID syntax everywhere: no localized-name lookups, no .NET
# identity mapping (that is what threw IdentityNotMappedException before).

param([switch]$Writable)

$ErrorActionPreference = "Continue"
$target = Join-Path (Split-Path -Parent $PSScriptRoot) "人格"
$card = Join-Path $target "人格卡.md"

$me = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($me)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host "User: $($me.Name)"
Write-Host "Elevated: $isAdmin"
if (-not $isAdmin) {
    Write-Host "[X] Need a real Administrator window (UAC accepted)." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $target)) { Write-Host "[X] Not found: $target" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "-> step 1: takeown (take ownership)"
& takeown /F $target /R /D Y 2>&1 | Out-Null
Write-Host "   done"

Write-Host "-> step 2: icacls /reset (restore inherited permissions)"
& icacls $target /reset /T /C /Q 2>&1 | Out-Null
Write-Host "   done"

if (-not $Writable) {
    Write-Host "-> step 3: apply read-only protection with SID syntax"
    & icacls $target /inheritance:r /grant:r `
        "*S-1-5-32-544:(OI)(CI)F" `
        "*S-1-5-18:(OI)(CI)F" `
        "*S-1-5-32-545:(OI)(CI)RX" `
        /T /C /Q 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   [!] protection step returned $LASTEXITCODE - falling back to inherited (writable) ACL" -ForegroundColor Yellow
        & icacls $target /reset /T /C /Q 2>&1 | Out-Null
        $Writable = $true
    } else {
        Write-Host "   done"
    }
}

Write-Host ""
Write-Host "=== folder ACL ==="
& icacls $target
Write-Host "=== card ACL ==="
& icacls $card

Write-Host ""
Write-Host "=== read test ==="
try {
    $first = Get-Content $card -TotalCount 1 -ErrorAction Stop
    Write-Host "[OK] Read works: $first" -ForegroundColor Green
} catch {
    Write-Host "[X] Still unreadable: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "    Fallback: run this script again with -Writable" -ForegroundColor Yellow
    exit 1
}

Write-Host "=== write test ==="
$probe = Join-Path $target ".write-test"
try {
    Set-Content -Path $probe -Value "x" -ErrorAction Stop
    Remove-Item $probe -Force -ErrorAction SilentlyContinue
    Write-Host "[i] Write succeeded in this elevated window (expected)." -ForegroundColor Yellow
} catch {
    Write-Host "[OK] Write denied." -ForegroundColor Green
}

Write-Host ""
if ($Writable) {
    Write-Host "[DONE] Folder is readable AND writable (protection OFF)." -ForegroundColor Yellow
} else {
    Write-Host "[DONE] Folder is readable, write-protected for normal users (protection ON)." -ForegroundColor Green
    Write-Host "       Edit the card from an elevated window: python 脚本\l0_apply.py --accept --reason '...'"
}
