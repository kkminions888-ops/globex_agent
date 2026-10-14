$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\laboratory\code\globex-agent'
$baseline = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'acl-before.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$allowedPaths = @(
    (Join-Path $projectRoot 'data\.secrets'),
    (Join-Path $projectRoot 'data\.secrets\model_registry.key')
)
$account = New-Object System.Security.Principal.NTAccount('IZHOU', '14580')
$targetSid = $account.Translate([System.Security.Principal.SecurityIdentifier]).Value
foreach ($entry in $baseline) {
    if ($allowedPaths -notcontains $entry.path) { throw '回滚目标超出本次授权范围。' }
    if ($entry.sddl.Contains($targetSid)) { throw '原始权限包含该账户，请人工按基线恢复，避免误删旧授权。' }
    # 原始 DACL 不含该账户；只撤销本次添加的显式 Allow，不改其他规则。
    & icacls.exe $entry.path /remove:g ('*' + $targetSid)
    if ($LASTEXITCODE -ne 0) { throw '撤销授权失败，请使用原所有者或管理员账户执行。' }
}
Write-Output '已撤销本次两个只读授权；没有修改密钥内容、所有者或其他账户权限。'
