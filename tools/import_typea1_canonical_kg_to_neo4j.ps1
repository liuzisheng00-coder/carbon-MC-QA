<#
Import the complete TypeA1 canonical carbon knowledge graph into the local
Neo4j Desktop database shown in the DM2C walkthrough.

This script intentionally clears only the target database after an explicit
interactive confirmation. It then executes the generated Cypher file through
the instance's bundled cypher-shell and prints post-import verification counts.
#>

[CmdletBinding()]
param(
    [string]$CypherShellPath = "C:\Users\liuzi\.Neo4jDesktop2\Data\dbmss\dbms-d0cdf214-4d2b-4659-a57a-9d34de8163e4\bin\cypher-shell.bat",
    [string]$GraphScriptPath = "",
    [string]$Address = "neo4j://127.0.0.1:7687",
    [string]$Database = "neo4j",
    [string]$Username = "neo4j"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($GraphScriptPath)) {
    $workspaceRoot = Split-Path -Parent $PSScriptRoot
    $GraphScriptPath = Join-Path $workspaceRoot "outputs\research_experiments\m2_typea1_full_en_layerdedup_20260731_d_spread\multigranular_carbon_kg.cypher"
}

if (-not (Test-Path -LiteralPath $CypherShellPath -PathType Leaf)) {
    throw "cypher-shell was not found: $CypherShellPath"
}
if (-not (Test-Path -LiteralPath $GraphScriptPath -PathType Leaf)) {
    throw "Canonical TypeA1 Cypher file was not found: $GraphScriptPath"
}

Write-Host "Target database: $Address / $Database" -ForegroundColor Yellow
Write-Host "This will permanently remove every current node and relationship in '$Database'." -ForegroundColor Red
$confirmation = Read-Host "Type CLEAR_AND_IMPORT to continue"
if ($confirmation -cne "CLEAR_AND_IMPORT") {
    Write-Host "Cancelled. The database was not changed." -ForegroundColor Yellow
    exit 0
}

$securePassword = Read-Host "Neo4j password for $Username" -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)

try {
    $env:NEO4J_ADDRESS = $Address
    $env:NEO4J_DATABASE = $Database
    $env:NEO4J_USERNAME = $Username
    $env:NEO4J_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)

    Write-Host "Current database size:" -ForegroundColor Cyan
    & $CypherShellPath --format plain "MATCH (n) RETURN count(n) AS nodes;"
    if ($LASTEXITCODE -ne 0) { throw "Could not connect to the local Neo4j database." }
    & $CypherShellPath --format plain "MATCH ()-[r]->() RETURN count(r) AS relationships;"
    if ($LASTEXITCODE -ne 0) { throw "Could not read the current relationship count." }

    Write-Host "Clearing the existing IFC backbone graph..." -ForegroundColor Yellow
    & $CypherShellPath --format plain "MATCH (n) DETACH DELETE n;"
    if ($LASTEXITCODE -ne 0) { throw "The target database was not cleared; import was not started." }

    Write-Host "Importing the complete TypeA1 canonical carbon KG. This may take a few minutes..." -ForegroundColor Cyan
    & $CypherShellPath --file $GraphScriptPath --format plain --fail-fast
    if ($LASTEXITCODE -ne 0) { throw "Cypher import failed. Inspect the command output before retrying." }

    Write-Host "Post-import verification:" -ForegroundColor Green
    & $CypherShellPath --format plain "MATCH (n) RETURN count(n) AS nodes;"
    & $CypherShellPath --format plain "MATCH ()-[r]->() RETURN count(r) AS relationships;"
    & $CypherShellPath --format plain "MATCH (n:DM2CEntity) RETURN count(n) AS canonicalNodes;"
    if ($LASTEXITCODE -ne 0) { throw "Import completed, but the verification query failed." }

    Write-Host "Expected result: 2993 nodes, 5613 relationships, and 2993 canonicalNodes." -ForegroundColor Green
}
finally {
    if ($passwordPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
    }
    Remove-Item Env:NEO4J_ADDRESS -ErrorAction SilentlyContinue
    Remove-Item Env:NEO4J_DATABASE -ErrorAction SilentlyContinue
    Remove-Item Env:NEO4J_USERNAME -ErrorAction SilentlyContinue
    Remove-Item Env:NEO4J_PASSWORD -ErrorAction SilentlyContinue
}
