# Passo 29 - privacidade do Gestor e do mapa (LGPD)

## O que muda (as duas decisoes que estavam abertas)

| # | Mudanca | Onde |
|---|---------|------|
| 1 | O **Gestor** passa a receber o atendimento **sem identificar o paciente**: nome vira um codigo (ex.: "Paciente 3F9A12"), sem data de nascimento, relato, rua, arquivos de audio/foto nem parecer; coordenadas com 2 casas (cerca de 1 km). Sexo, sintomas, risco, bairro e municipio continuam. **ACS e Medico nao mudam.** | `app/services/privacidade.py`, `app/routers/atendimentos.py` |
| 2 | **Localidade com menos de 3 casos** sai no mapa com a coordenada menos precisa (2 casas, cerca de 1 km, em vez de 3 casas, cerca de 110 m) e marcada como `localizacao_aproximada`. Localidade com 3 ou mais casos continua como antes. | `app/routers/mapa.py` |

Assim o texto de LGPD do codigo (o Gestor trabalha com dados agregados) passa a ser verdade tambem em `GET /atendimentos`.
O front nao precisa mudar: o painel do Gestor nao usa essas listas identificadas, so o mapa e os numeros.

Para mudar o limite de 3 casos, edite `K_MINIMO` em `app/services/privacidade.py`.

## Aplicar (5 minutos)

1. Extraia o zip numa pasta (ex.: `Downloads\passo29`) e, no PowerShell, na pasta do projeto:

```powershell
cd "C:\Users\HYDRO\OneDrive\Documentos\PROJETO MEDGRAPH-AM\medgraph-backend"
Copy-Item "$env:USERPROFILE\Downloads\passo29\*" . -Recurse -Force
python scripts\aplicar_passo29.py
```

Deve mostrar "aplicado" em 4 arquivos. Se der ERRO, me mande a foto (nada e gravado no arquivo com problema).

2. Reinicie e teste (esperado: **272 passed** = 264 + 8 novos):

```powershell
docker compose up -d --build
docker compose exec api pytest -q
```

3. Publique:

```powershell
git add -A
git status -s
```

Confira que NAO aparecem `.env`, `*.sql`, `*.bak-*` nem `tunel.txt`. Depois:

```powershell
git commit -m "Passo 29: Gestor sem identificar o paciente; localizacao aproximada em localidades com poucos casos"
git push
```
