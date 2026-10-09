# MEDGRAPH-AM - desliga o tunel e o back-end (os dados dos bancos ficam guardados).
# Use pelo arquivo desligar.bat (duplo clique).

$ErrorActionPreference = 'Continue'
Set-Location -Path $PSScriptRoot

Write-Host '=== MEDGRAPH-AM: desligando ===' -ForegroundColor Cyan

Write-Host 'Fechando o tunel...'
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host 'Parando API, PostgreSQL e Neo4j (os dados ficam guardados)...'
docker compose stop

Write-Host ''
Write-Host 'Pronto. Pode fechar o Docker Desktop se quiser.' -ForegroundColor Green
Write-Host '(Nunca use "docker compose down -v": isso apaga os bancos.)' -ForegroundColor Yellow
Write-Host ''
Read-Host 'Aperte Enter para fechar'
