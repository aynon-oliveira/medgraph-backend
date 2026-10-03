# MEDGRAPH-AM — Back-end

API em FastAPI com PostgreSQL/PostGIS e Neo4j, rodando em Docker.

## Como rodar

1. Instale o Docker Desktop.
2. Copie as variáveis de ambiente e troque as senhas:
   ```bash
   cp .env.example .env
   ```
3. Suba tudo:
   ```bash
   docker compose up --build
   ```
4. Abra no navegador:
   - Documentação automática da API: http://localhost:8000/docs
   - Saúde dos serviços: http://localhost:8000/health (deve mostrar `ok` para api, postgres, postgis e neo4j)
   - Neo4j Browser: http://localhost:7474

## Estrutura

```
app/
  core/       configuração, banco e (depois) segurança
  models/     tabelas do banco (SQLAlchemy)
  schemas/    validação de entrada e saída (Pydantic)
  routers/    rotas da API
  services/   regras de negócio
tests/        testes automatizados (pytest)
```

## Próximos passos

4. Autenticação com JWT e perfis (RF01)
5. CRUD de atendimentos (RF02, RN05)
6. Sincronização POST /api/v1/sincronizar (RF07, RN03, RN06)
7. Validação médica (RF08)
8. Integração com Neo4j (RF09)
9. Mapa de risco em GeoJSON (RF10)
