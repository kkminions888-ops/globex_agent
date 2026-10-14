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
$targets = @($secretDirectory, $keyPath)
$before = @($targets | ForEach-Object {
    $acl = Get-Acl -LiteralPath $_
    [pscustomobject]@{ path = $_; owner = $acl.Owner; sddl = $acl.Sddl }
})
if (Test-Path -LiteralPath $baselinePath) {
    $original = Get-Content -LiteralPath $baselinePath -Raw -Encoding UTF8 | ConvertFrom-Json
    for ($index = 0; $index -lt $targets.Count; $index++) {
        if ($original[$index].path -ne $before[$index].path -or $original[$index].sddl -ne $before[$index].sddl) {
            throw '当前权限已偏离原始基线，停止修改。'
        }
    }
} else {
    [System.IO.File]::WriteAllText($baselinePath, (ConvertTo-Json -InputObject $before -Depth 5), $utf8)
}
$keyBefore = [System.IO.File]::ReadAllBytes($keyPath)
$envPath = Join-Path $projectRoot '.env'
$envBefore = [System.IO.File]::ReadAllBytes($envPath)
$keyItemBefore = Get-Item -LiteralPath $keyPath -Force
$keyModifiedBefore = $keyItemBefore.LastWriteTimeUtc

# icacls /grant 只修改 DACL；不递归、不改所有者，也不给文件写权限。
& icacls.exe $secretDirectory /grant ('*' + $sid.Value + ':(RX)')
if ($LASTEXITCODE -ne 0) { throw '目录权限写入失败。' }
& icacls.exe $keyPath /grant ('*' + $sid.Value + ':(R)')
if ($LASTEXITCODE -ne 0) { throw '密钥只读权限写入失败。' }

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
