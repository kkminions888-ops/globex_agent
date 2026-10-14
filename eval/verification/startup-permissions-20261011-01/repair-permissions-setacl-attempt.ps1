$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\laboratory\code\globex-agent'
$evidenceDir = Join-Path $projectRoot 'eval\verification\startup-permissions-20261011-01'
$secretDirectory = Join-Path $projectRoot 'data\.secrets'
$keyPath = Join-Path $secretDirectory 'model_registry.key'
$account = New-Object System.Security.Principal.NTAccount('IZHOU', '14580')
$sid = $account.Translate([System.Security.Principal.SecurityIdentifier])
$utf8 = New-Object System.Text.UTF8Encoding($false)

# 只保存访问控制信息和非敏感元数据，不记录密钥、数据库内容或凭据摘要。
$baselinePath = Join-Path $evidenceDir 'acl-before.json'
if (Test-Path -LiteralPath $baselinePath) { throw '基线已存在，拒绝覆盖历史证据。' }
$targets = @($secretDirectory, $keyPath)
$before = @($targets | ForEach-Object {
    $acl = Get-Acl -LiteralPath $_
    [pscustomobject]@{ path = $_; owner = $acl.Owner; sddl = $acl.Sddl }
})
[System.IO.File]::WriteAllText($baselinePath, (ConvertTo-Json -InputObject $before -Depth 5), $utf8)
$keyBefore = [System.IO.File]::ReadAllBytes($keyPath)
$envPath = Join-Path $projectRoot '.env'
$envBefore = [System.IO.File]::ReadAllBytes($envPath)
$keyItemBefore = Get-Item -LiteralPath $keyPath -Force
$keyModifiedBefore = $keyItemBefore.LastWriteTimeUtc

$directoryAcl = Get-Acl -LiteralPath $secretDirectory
$directoryRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $sid,
    [System.Security.AccessControl.FileSystemRights]::ReadAndExecute,
    [System.Security.AccessControl.InheritanceFlags]::None,
    [System.Security.AccessControl.PropagationFlags]::None,
    [System.Security.AccessControl.AccessControlType]::Allow
)
$directoryAcl.AddAccessRule($directoryRule)
Set-Acl -LiteralPath $secretDirectory -AclObject $directoryAcl

$keyAcl = Get-Acl -LiteralPath $keyPath
$keyRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $sid,
    [System.Security.AccessControl.FileSystemRights]::Read,
    [System.Security.AccessControl.AccessControlType]::Allow
)
$keyAcl.AddAccessRule($keyRule)
Set-Acl -LiteralPath $keyPath -AclObject $keyAcl

$after = @($targets | ForEach-Object {
    $acl = Get-Acl -LiteralPath $_
    [pscustomobject]@{ path = $_; owner = $acl.Owner; sddl = $acl.Sddl }
})
[System.IO.File]::WriteAllText((Join-Path $evidenceDir 'acl-after.json'), (ConvertTo-Json -InputObject $after -Depth 5), $utf8)
$keyAfter = [System.IO.File]::ReadAllBytes($keyPath)
$envAfter = [System.IO.File]::ReadAllBytes($envPath)
$result = [pscustomobject]@{
    timestamp = [DateTimeOffset]::UtcNow.ToString('o')
    operator = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    target_account = $account.Value
    target_sid = $sid.Value
    directory_rights = 'ReadAndExecute; no inheritance'
    key_rights = 'Read'
    key_bytes_unchanged = [Convert]::ToBase64String($keyBefore) -eq [Convert]::ToBase64String($keyAfter)
    key_mtime_unchanged = (Get-Item -LiteralPath $keyPath -Force).LastWriteTimeUtc -eq $keyModifiedBefore
    env_bytes_unchanged = [Convert]::ToBase64String($envBefore) -eq [Convert]::ToBase64String($envAfter)
    ownership_unchanged = ($before[0].owner -eq $after[0].owner -and $before[1].owner -eq $after[1].owner)
}
[System.IO.File]::WriteAllText((Join-Path $evidenceDir 'repair-result.json'), ($result | ConvertTo-Json -Depth 5), $utf8)
$result | ConvertTo-Json -Compress
if (-not $result.key_bytes_unchanged -or -not $result.env_bytes_unchanged -or -not $result.ownership_unchanged) {
    throw '保留性检查失败，请查看证据并停止进一步操作。'
}
