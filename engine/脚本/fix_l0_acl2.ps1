# fix_l0_acl2.ps1 - rebuild ACL per-item with .NET SID objects (run as Administrator)
#
# Why a second script: `icacls /T` and `/reset` do not repair children whose DACL
# is "protected + empty" (that is the state the card file ended up in). This script
# sets the DACL explicitly on the folder AND on every child, using
# SecurityIdentifier objects (not name strings -> no IdentityNotMappedException).
#
#   powershell -ExecutionPolicy Bypass -File .\脚本\fix_l0_acl2.ps1            # read-only protection
#   powershell -ExecutionPolicy Bypass -File .\脚本\fix_l0_acl2.ps1 -Writable  # everyone writable

param([switch]$Writable)

$ErrorActionPreference = "Continue"
$target = Join-Path (Split-Path -Parent $PSScriptRoot) "人格"
$card = Join-Path $target "人格卡.md"

$me = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($me)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Host "User: $($me.Name)"
Write-Host "Elevated: $isAdmin"
if (-not $isAdmin) { Write-Host "[X] Need Administrator." -ForegroundColor Red; exit 1 }
if (-not (Test-Path $target)) { Write-Host "[X] Not found: $target" -ForegroundColor Red; exit 1 }

$sidAdmin = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')
$sidSys   = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
$sidUsers = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-545')

function Set-ItemAcl([string]$path, [bool]$isDir) {
    $inherit = if ($isDir) {
        [System.Security.AccessControl.InheritanceFlags]"ContainerInherit, ObjectInherit"
    } else { [System.Security.AccessControl.InheritanceFlags]::None }
    $none = [System.Security.AccessControl.PropagationFlags]::None
    $allow = [System.Security.AccessControl.AccessControlType]::Allow

    if ($isDir) {
        $acl = New-Object System.Security.AccessControl.DirectorySecurity
    } else {
        $acl = New-Object System.Security.AccessControl.FileSecurity
    }
    $acl.SetAccessRuleProtection($true, $false)      # protected DACL, drop inherited
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sidAdmin, 'FullControl', $inherit, $none, $allow)))
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sidSys,   'FullControl', $inherit, $none, $allow)))
    $usersRight = if ($Writable) { 'Modify' } else { 'ReadAndExecute' }
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sidUsers, $usersRight, $inherit, $none, $allow)))
    $acl.SetOwner($sidAdmin)
    try {
        Set-Acl -Path $path -AclObject $acl -ErrorAction Stop
        return $true
    } catch {
        Write-Host "   [X] $path -> $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

Write-Host ""
Write-Host "-> folder"
$ok = Set-ItemAcl $target $true
Write-Host "   $(if ($ok) {'done'} else {'FAILED'})"

Write-Host "-> children"
Get-ChildItem $target -Force | ForEach-Object {
    $r = Set-ItemAcl $_.FullName $_.PSIsContainer
    Write-Host ("   {0} {1}" -f $(if ($r) {'ok  '} else {'FAIL'}), $_.Name)
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
    exit 1
}

Write-Host ""
Write-Host "if read failed above, the last resort is: re-create the card file from a backup"
Write-Host "  (see 备份\L0\*.bak) - ask the assistant before doing that."
