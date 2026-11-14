param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$ExpectedVersion
)

$ErrorActionPreference = 'Stop'
$binary = Get-Item -LiteralPath $Path
if ($binary.PSIsContainer -or $binary.Length -le 0) {
    throw 'The signed release executable is missing or empty.'
}
$signature = Get-AuthenticodeSignature -LiteralPath $binary.FullName
if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) {
    throw "Signed release verification failed: $($signature.Status)."
}
$metadata = $binary.VersionInfo
if ($metadata.ProductName -cne 'Photo Review' -or
    $metadata.ProductVersion -cne $ExpectedVersion -or
    $metadata.FileVersion -cne $ExpectedVersion) {
    throw 'The signed executable product or version does not match this release.'
}
Write-Output "Verified signed Photo Review $ExpectedVersion"
Write-Output "Publisher: $($signature.SignerCertificate.Subject)"
