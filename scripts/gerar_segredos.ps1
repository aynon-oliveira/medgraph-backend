# Gera valores novos e aleatorios para o .env. NAO grava nada em disco: so mostra na tela.
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\gerar_segredos.ps1
function Aleatorio($n) {
    $letras = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    $b = New-Object byte[] $n
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    -join ($b | ForEach-Object { $letras[$_ % $letras.Length] })
}
$k = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($k)
Write-Host ''
Write-Host 'Copie estes valores para o seu .env (e guarde num lugar seguro, fora do GitHub):' -ForegroundColor Cyan
Write-Host ''
Write-Host ('SECRET_KEY=' + (($k | ForEach-Object { $_.ToString('x2') }) -join ''))
Write-Host ('POSTGRES_PASSWORD=' + (Aleatorio 24))
Write-Host ('NEO4J_PASSWORD=' + (Aleatorio 24))
Write-Host ''
Write-Host 'Trocar a SECRET_KEY desconecta todo mundo (e normal). Para o PostgreSQL e o Neo4j, siga a Parte C do LEIA-ME.' -ForegroundColor Yellow
