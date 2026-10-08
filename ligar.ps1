# MEDGRAPH-AM - liga tudo para o dia: Docker, back-end e tunel.
# Use pelo arquivo ligar.bat (duplo clique). Texto sem acentos de proposito (o Windows PowerShell 5.1 le arquivos sem BOM como ANSI).

$ErrorActionPreference = 'Continue'
Set-Location -Path $PSScriptRoot

function Dizer($texto, $cor = 'White') { Write-Host $texto -ForegroundColor $cor }
function Parar($texto) {
    Dizer ''
    Dizer $texto 'Red'
    Dizer ''
    Read-Host 'Aperte Enter para fechar'
    exit 1
}
function DockerPronto {
    docker info *> $null
    return ($LASTEXITCODE -eq 0)
}
function ApiSaudavel($endereco) {
    try {
        $r = Invoke-WebRequest -Uri ($endereco + '/health') -UseBasicParsing -TimeoutSec 8
        return (($r.Content -match '"postgres"\s*:\s*"ok"') -and ($r.Content -match '"neo4j"\s*:\s*"ok"'))
    } catch {
        return $false
    }
}
function EsperarApi($endereco, $tentativas) {
    for ($i = 0; $i -lt $tentativas; $i++) {
        if (ApiSaudavel $endereco) { return $true }
        Write-Host '.' -NoNewline
        Start-Sleep -Seconds 5
    }
    return $false
}

Dizer '=== MEDGRAPH-AM: ligando ===' 'Cyan'
Dizer ('Pasta: ' + $PSScriptRoot)
if (-not (Test-Path (Join-Path $PSScriptRoot 'docker-compose.yml'))) {
    Parar 'Nao achei o docker-compose.yml aqui. Coloque o ligar.bat DENTRO da pasta medgraph-backend.'
}

# 1) Docker Desktop
Dizer ''
Dizer '1/4  Docker Desktop...'
if (-not (DockerPronto)) {
    Dizer '     Docker desligado. Abrindo o Docker Desktop (pode levar 1 a 3 minutos)...' 'Yellow'
    $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $exe) { Start-Process -FilePath $exe } else { Dizer '     Nao achei o Docker Desktop no lugar padrao. Abra-o voce mesma; vou esperar.' 'Yellow' }
    $pronto = $false
    for ($i = 0; $i -lt 48; $i++) {
        Start-Sleep -Seconds 5
        if (DockerPronto) { $pronto = $true; break }
        Write-Host '.' -NoNewline
    }
    Dizer ''
    if (-not $pronto) { Parar 'O Docker nao ficou pronto em 4 minutos. Abra o Docker Desktop, espere a baleia parar e rode o ligar.bat de novo.' }
}
Dizer '     Docker pronto.' 'Green'

# 2) Back-end
Dizer ''
Dizer '2/4  Ligando API, PostgreSQL e Neo4j...'
docker compose up -d
if ($LASTEXITCODE -ne 0) { Parar 'O docker compose up -d falhou. Veja a mensagem acima e me mande uma foto.' }

Dizer ''
Dizer '3/4  Esperando a API responder (/health)...'
$local = 'http://127.0.0.1:8000'
$apiOk = EsperarApi $local 24
if (-not $apiOk) {
    Dizer ''
    Dizer '     A API nao respondeu. Reiniciando so a API (acontece quando ela sobe antes da rede do Docker)...' 'Yellow'
    docker compose restart api | Out-Null
    $apiOk = EsperarApi $local 24
}
Dizer ''
if (-not $apiOk) {
    docker compose logs api --tail 25
    Parar 'A API nao ficou saudavel. Me mande uma foto das linhas acima.'
}
Dizer '     API, PostgreSQL, PostGIS e Neo4j: ok.' 'Green'

# 3) Tunel
Dizer ''
Dizer '4/4  Ligando o tunel...'
$cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cmd) { Parar 'O cloudflared nao foi encontrado neste computador (comando "cloudflared").' }
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

$log = Join-Path $env:TEMP 'medgraph_tunel.log'
Remove-Item $log -ErrorAction SilentlyContinue
Start-Process -FilePath $cmd.Source -ArgumentList @('tunnel', '--url', 'http://127.0.0.1:8000', '--logfile', $log) -WindowStyle Minimized

$url = $null
for ($i = 0; $i -lt 40 -and -not $url; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path $log) {
        $linhas = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -AllMatches -ErrorAction SilentlyContinue
        foreach ($linha in $linhas) {
            foreach ($achado in $linha.Matches) {
                if (-not $url -and $achado.Value -notmatch '//api\.') { $url = $achado.Value }
            }
        }
    }
}
if (-not $url) { Parar 'Nao consegui descobrir o endereco do tunel. Abra a janela minimizada do cloudflared (barra de tarefas) e procure o endereco .trycloudflare.com.' }

Dizer '     Testando o tunel (pode levar ate 1 minuto para o endereco "pegar")...'
$tunelOk = EsperarApi $url 12
Dizer ''
if ($tunelOk) { Dizer '     Tunel funcionando.' 'Green' } else { Dizer '     O tunel ainda nao respondeu. Espere um pouco e abra o endereco + /health no navegador.' 'Yellow' }

try { Set-Clipboard -Value $url } catch { }

Dizer ''
Dizer '==============================================================' 'Cyan'
Dizer ' PRONTO. Endereco do servidor de hoje (ja copiado):' 'Cyan'
Dizer ''
Dizer ('   ' + $url) 'Green'
Dizer ''
Dizer ' No site: caixa "Servidor" > cole o endereco > "Usar este servidor".' 'White'
Dizer ' Deixe o tunel aberto (janela minimizada do cloudflared) e nao deixe' 'White'
Dizer ' o computador entrar em suspensao.' 'White'
Dizer '==============================================================' 'Cyan'

try { Start-Process 'https://medgraph-am.netlify.app' } catch { }
Dizer ''
Read-Host 'Pode fechar esta janela com Enter (o tunel continua aberto na outra)'
