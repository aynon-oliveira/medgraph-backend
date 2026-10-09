# Passo 28 - segurança do back-end (antes da pré-banca)

## O que muda

| # | Mudança | Onde |
|---|---------|------|
| 1 | Aviso no `ligar.ps1` se o servidor ainda estiver em modo desenvolvimento (e passo a passo para ligar o modo produção) | `ligar.ps1`, Parte B |
| 2 | PostgreSQL e Neo4j só aceitam conexões deste computador (não mais pela rede local) | `docker-compose.yml` |
| 3 | Limite de tentativas de login: 5 erros seguidos para o mesmo e-mail = 5 minutos de bloqueio | `app/core/limitador.py`, `app/routers/auth.py` |
| 4 | `.dockerignore`: o `.env` e a `.venv-ml` deixam de entrar dentro da imagem Docker | `.dockerignore` |
| 5 | Fotos perdem GPS, modelo do celular e data (EXIF) ao serem salvas e antes de irem ao Gemini. A imagem em si não muda | `app/services/imagem_limpa.py`, `midia_service.py`, `assistente.py` |
| 6 | `/health` não mostra mais pedaços de mensagens de erro (vão só para o log) | `app/routers/health.py` |

Fica para você decidir (não mexi): se o Gestor continua vendo o paciente identificado e como tratar localidades com 1 caso.

---

## Parte A - aplicar (10 minutos)

1. Extraia o zip numa pasta (ex.: `Downloads\passo28`).
2. No PowerShell, na pasta do projeto:

```powershell
cd "C:\Users\HYDRO\OneDrive\Documentos\PROJETO MEDGRAPH-AM\medgraph-backend"
Copy-Item "$env:USERPROFILE\Downloads\passo28\*" . -Recurse -Force
python scripts\aplicar_passo28.py
```

O script mostra uma linha por arquivo ("aplicado" ou "já estava aplicado") e faz cópia de segurança `*.bak-passo28`. Se ele parar com ERRO, me mande a foto da mensagem; nada é gravado naquele arquivo.

3. Reinicie e rode os testes:

```powershell
docker compose up -d --build
docker compose exec api pytest -q
```

O esperado é tudo passando (os 253 de antes + 11 novos = 264).

4. Publique no GitHub:

```powershell
git add -A
git status -s
```

Confira que NÃO aparecem `.env`, `*.sql` nem `tunel.txt`. Depois:

```powershell
git commit -m "Passo 28: seguranca (limite de login, bancos so locais, fotos sem EXIF, dockerignore)"
git push
```

---

## Parte B - modo produção (esconde o /docs e recusa senha fraca)

Só faça DEPOIS da Parte C (senão a API se recusa a subir, de propósito).

```powershell
Add-Content .env "`nAMBIENTE=producao"
docker compose up -d
```

Conferir:

```powershell
docker compose exec api python -c "from app.core.config import settings; print(settings.problemas_de_seguranca() or 'tudo certo')"
```

Se aparecer uma lista, é o que ainda está fraco no `.env`. Para confirmar que o `/docs` fechou, abra `http://127.0.0.1:8000/docs`: deve dar "Not Found".
Se a API não subir: `docker compose logs api --tail 20` mostra o motivo.

---

## Parte C - trocar as senhas e a chave (o que já estava na sua lista)

Importante: o PostgreSQL e o Neo4j só leem a senha do `.env` na PRIMEIRA criação. Trocar só o `.env` faz a API perder o acesso aos bancos. Por isso a ordem abaixo.

1. (Opcional, recomendado) backup fora da pasta do projeto, para não ir ao GitHub:

```powershell
cmd /c "docker compose exec -T db pg_dump -U medgraph medgraph > %USERPROFILE%\backup-medgraph.sql"
```
(troque `medgraph` pelo seu `POSTGRES_USER`/banco, se for diferente; apague o arquivo quando terminar: tem dados de pacientes).

2. Gerar valores novos:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\gerar_segredos.ps1
```

3. Trocar a senha DENTRO do PostgreSQL e do Neo4j (use a senha nova que o script mostrou; a antiga está no seu `.env`):

```powershell
docker compose exec db psql -U medgraph -d medgraph -c "ALTER USER medgraph WITH PASSWORD 'POSTGRES_NOVA';"
docker compose exec neo4j cypher-shell -u neo4j -p "NEO4J_ANTIGA" "ALTER CURRENT USER SET PASSWORD FROM 'NEO4J_ANTIGA' TO 'NEO4J_NOVA';"
```

4. Só agora edite o `.env` (`notepad .env`) e troque as três linhas: `SECRET_KEY`, `POSTGRES_PASSWORD`, `NEO4J_PASSWORD`. Salve.

5. Reinicie e teste:

```powershell
docker compose up -d
Invoke-RestMethod http://127.0.0.1:8000/health
```

Deve mostrar `ok` nos quatro itens. Todo mundo precisará entrar de novo (a chave mudou): é normal.

6. Depois, no site, gere novas senhas temporárias dos usuários de teste (Usuários > Redefinir senha) e cadastre um segundo gestor.

Ainda fora do código: apagar no Google AI Studio as chaves antigas do Gemini (deixe uma só, e ponha a nova em `GEMINI_API_KEY`) e gerar um novo authtoken no painel do ngrok (`ngrok config add-authtoken NOVO`).
