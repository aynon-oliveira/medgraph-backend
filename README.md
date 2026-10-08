# MEDGRAPH-AM — Back-end

API em FastAPI com PostgreSQL/PostGIS e Neo4j, em Docker. Apoia a triagem precoce e o monitoramento epidemiológico
da Dengue no Amazonas, com aplicativo *offline-first* para três perfis: ACS, Médico e Gestor.

> **O que este repositório contém:** a API, o banco, o grafo, a sincronização, a validação médica, o mapa e a
> auditoria de acessos. **O que ainda não está aqui:** o módulo de IA (Whisper, visão computacional, GCN/GAT) e o
> aplicativo móvel nativo. A IA é apoio à decisão: quem decide é o profissional de saúde (RN01).

## Como rodar (desenvolvimento)

1. Instale o Docker Desktop.
2. Copie as variáveis e **troque todas as senhas e a `SECRET_KEY`** (nunca envie o `.env` ao GitHub):
   ```
   cp .env.example .env
   python -c "import secrets; print(secrets.token_hex(32))"   # cole em SECRET_KEY=
   ```
3. Suba: `docker compose up --build`
4. Abra: documentação http://localhost:8000/docs · saúde http://localhost:8000/health · Neo4j http://localhost:7474
5. Crie o primeiro usuário (o primeiro precisa ser GESTOR) em `POST /auth/registrar`. Depois só o Gestor cadastra outros.

## Modo produção / demonstração

    docker compose down
    docker compose -f docker-compose.prod.yml up -d --build

Com `AMBIENTE=producao` a API **recusa subir** com chave ou senhas de exemplo, esconde o `/docs` e deixa os bancos
fechados para fora do Docker. Não rode os dois modos juntos (ambos usam a porta 8000). O modo produção usa volumes
próprios (banco vazio): crie o GESTOR de novo.

## Testes

    docker compose exec api pytest -q

Há testes de autenticação, perfis, atendimentos, sincronização, validação, grafo, mapa, mídia, CORS, configuração
segura, gestão de usuários, projeção e auditoria. Alguns usam PostgreSQL e Neo4j de verdade, por isso rodam
**dentro do container**. Eles limpam os próprios dados ao terminar.

## Principais rotas

| Área | Rotas | Quem |
|---|---|---|
| Acesso | `POST /auth/login` (e-mail + senha), `GET /auth/me`, `POST /auth/trocar-senha` | todos |
| Usuários | `POST /auth/registrar`, `GET/PATCH /usuarios`, desativar, reativar, redefinir senha | Gestor |
| Atendimentos | `POST/GET /atendimentos`, `GET /atendimentos/{id}` | ACS (próprios), Médico e Gestor (todos) |
| Áudio e foto | `PUT/GET /atendimentos/{id}/midia/{audio\|foto}` | ACS envia; ACS e Médico baixam (Gestor não) |
| Sincronização | `POST /api/v1/sincronizar` (idempotente, ALTO risco primeiro) | ACS |
| Validação | `GET /validacoes/fila`, `POST /atendimentos/{id}/validacao` | Médico |
| Grafo | `/grafo/resumo`, `/grafo/sintomas/frequencia`, `/grafo/localidades`, `/grafo/focos`, `/grafo/reconstruir` | leitura: Médico e Gestor; registrar foco e reconstruir: só Gestor |
| Mapa | `GET /api/v1/mapa/atendimentos`, `/densidade`, `/projecao` (GeoJSON) | Gestor e Médico |
| Auditoria (LGPD) | `GET /auditoria/acessos` | Gestor |

## Regras de negócio implementadas

RN01 IA só apoia a decisão · RN02 qualquer sinal de alarme vira ALTO risco · RN03 ALTO risco sincroniza primeiro ·
RN04 relações do grafo nunca são apagadas · RN05 latitude e longitude obrigatórias · RN06 parecer do médico prevalece;
entre dados do mesmo nível vale o `atualizado_em` mais recente.

## Privacidade (LGPD) em resumo

- PostgreSQL é a fonte da verdade; o grafo é uma projeção reconstruível, sem nome nem documento do paciente.
- O mapa devolve coordenadas arredondadas (3 casas, cerca de 110 m) e nunca o id do paciente.
- O Gestor não baixa foto nem áudio; vê dados agregados.
- Toda consulta de Médico ou Gestor a dados de saúde (abrir, listar, baixar mídia, ver fila, validar) gera um
  registro em `registros_acesso`, com quem, quando, o quê e o IP, **sem o conteúdo**. O Gestor consulta em
  `/auditoria/acessos`. A API não permite apagar nem alterar esses registros. O ACS não gera registro por ver os
  próprios atendimentos. Se a gravação do registro falhar, a consulta segue e o erro vai para o log do servidor.
- Senhas: bcrypt; token JWT de 60 min; desativar usuário ou trocar a senha derruba as sessões na hora.
- Em produção, atrás de proxy, o IP registrado é o do proxy (o cabeçalho `X-Forwarded-For` não é confiável).

## Resultados medidos (sincronização, RNF02)

Simulação controlada de falhas de rede contra a API real (`tests/medir_sincronizacao.py`), 500 atendimentos por
rodada e 35% de quedas depois da gravação: sementes 1, 2 e 3 deram **100%** de sincronização, com 0 perdidos,
0 duplicados e 0 corrompidos. A primeira medição revelou uma paginação instável na listagem (empates na
ordenação); foi corrigida. **Isto não substitui o teste em aparelho físico (TI02/TS02).**

## Estrutura

```
app/
  core/       configuração, banco, segurança, dependências
  models/     tabelas (SQLAlchemy)
  schemas/    validação de entrada e saída (Pydantic)
  routers/    rotas da API
  services/   regras de negócio (grafo, mídia, projeção de surtos, auditoria)
tests/        testes automatizados e script de medição
uploads/      volume Docker com áudio e foto (fora do banco)
```

## Limites conhecidos

- A projeção de surtos é uma reta ajustada às contagens semanais (linha de base), não o modelo GCN/GAT.
- Novas colunas em tabelas já existentes são adicionadas por `ALTER TABLE ... IF NOT EXISTS` ao subir a API;
  não há migrações com Alembic.
- Não há limite de tentativas de login nem renovação de token (*refresh*).
