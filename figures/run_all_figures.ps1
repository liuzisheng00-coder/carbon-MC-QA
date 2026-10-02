$node = 'C:\Users\liuzi\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$env:NODE_PATH = 'C:\Users\liuzi\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
Get-ChildItem -Path $PSScriptRoot -Filter 'gen_fig_*.js' | Sort-Object Name | ForEach-Object {
    & $node $_.FullName
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& $node (Join-Path $PSScriptRoot 'export_pdf.js')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
