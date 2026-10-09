# Redefine a senha do usuario do Neo4j para o valor NEO4J_PASSWORD que ja esta no .env.
# Nao precisa da senha antiga. Os dados do grafo nao sao mexidos.
# Uso, na pasta do projeto:  powershell -ExecutionPolicy Bypass -File scripts\resetar_neo4j.ps1
$ErrorActionPreference = 'Continue'
$envPath = Join-Path (Get-Location) '.env'
if (-not (Test-Path $envPath)) { Write-Host 'ERRO: rode na pasta do projeto (onde esta o .env).' -ForegroundColor Red; exit 1 }
$utf8 = New-Object Text.UTF8Encoding $false
$linhas = [IO.File]::ReadAllLines($envPath, $utf8)
function Pegar($chave) {
    foreach ($l in $linhas) { if ($l -match ('^\s*' + $chave + '\s*=\s*(.*?)\s*$')) { return $Matches[1].Trim('"').Trim("'") } }
    return $null
}
$usuario = Pegar 'NEO4J_USER'; if (-not $usuario) { $usuario = 'neo4j' }
$senha = Pegar 'NEO4J_PASSWORD'
if (-not $senha -or $senha -notmatch '^[A-Za-z0-9]+$') { Write-Host 'ERRO: NEO4J_PASSWORD ausente ou com caracteres que nao sao letras/numeros.' -ForegroundColor Red; exit 1 }

Write-Host '1/4 Parando o Neo4j e abrindo uma copia temporaria sem senha (so dentro do Docker)...' -ForegroundColor Cyan
docker compose stop neo4j 2>&1 | Out-Null
docker rm -f neo4j_reset 2>&1 | Out-Null
docker compose run -d --no-deps --name neo4j_reset -e NEO4J_AUTH=none neo4j 2>&1 | Out-Null

Write-Host '2/4 Esperando o Neo4j temporario ficar pronto (ate 90 segundos)...' -ForegroundColor Cyan
$pronto = $false
for ($i = 0; $i -lt 18; $i++) {
    Start-Sleep -Seconds 5
    docker exec neo4j_reset cypher-shell "RETURN 1;" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $pronto = $true; break }
}
if (-not $pronto) {
    Write-Host 'ERRO: o Neo4j temporario nao ficou pronto. Ultimas linhas do log:' -ForegroundColor Red
    docker logs neo4j_reset --tail 15
    docker rm -f neo4j_reset 2>&1 | Out-Null
    exit 1
}

Write-Host '3/4 Gravando a senha nova...' -ForegroundColor Cyan
$cy = "ALTER USER $usuario SET PASSWORD '$senha' CHANGE NOT REQUIRED;"
$saida = docker exec neo4j_reset cypher-shell $cy 2>&1
$falhou = ($LASTEXITCODE -ne 0)
docker rm -f neo4j_reset 2>&1 | Out-Null
if ($falhou) {
    Write-Host 'ERRO ao gravar a senha. Saida:' -ForegroundColor Red
    $saida | ForEach-Object { Write-Host $_ }
    exit 1
}

Write-Host '4/4 Subindo tudo de novo e conferindo /health...' -ForegroundColor Cyan
docker compose up -d 2>&1 | Out-Null
$ok = $false
for ($i = 0; $i -lt 18; $i++) {
    Start-Sleep -Seconds 5
    try {
        $r = Invoke-RestMethod http://127.0.0.1:8000/health -TimeoutSec 5
        $txt = ($r | ConvertTo-Json -Compress)
        Write-Host $txt
        if ($txt -notmatch 'erro|falha|error|indispon') { $ok = $true; break }
    } catch { Write-Host '  ainda subindo...' }
}
Write-Host ''
if ($ok) { Write-Host 'Pronto. Neo4j com a senha nova e /health ok.' -ForegroundColor Green }
else { Write-Host 'O /health ainda nao esta todo ok. Me mande o print desta tela.' -ForegroundColor Yellow }
