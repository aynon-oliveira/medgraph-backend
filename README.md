# MEDGRAPH-AM — Back-end

Plataforma de **triagem de dengue offline-first** para o Amazonas, com três perfis de uso (Agente Comunitário de Saúde, Médico e Gestor). Projeto de TCC do curso de Ciência da Computação (FAMETRO/CEUNI).

> **Aviso:** o sistema é um apoio à triagem e à vigilância. Ele **não diagnostica** dengue e não substitui a avaliação clínica. A regra de alarme (sinais de alerta → prioridade alta) prevalece sobre qualquer pontuação do modelo.

- Site (front-end): https://medgraph-am.pages.dev
- Autores: Aynon Inhuma Oliveira e Francimar Vales
- Orientadora: Prof.ª Luana Magalhães Leal

---

## O que o sistema faz

| Perfil | O que faz |
|---|---|
| **ACS** | Registra atendimentos no campo (inclusive **sem internet**), calcula o risco no próprio aparelho e sincroniza depois. |
| **Médico** | Vê a ficha do atendimento, o nível de risco, os sinais de alarme e pode usar o assistente de IA (resumo e descrição de foto) como **rascunho**. |
| **Gestor** | Painel de casos, mapa, grafo de sintomas (Neo4j), auditoria e reconstrução do grafo. |

Principais recursos:

- Triagem com **sinais de alarme** e **modelo de risco** (regressão logística) que roda no navegador, sem internet.
- Campo opcional **"dias desde o início dos sintomas"** (0 a 30), usado pelo modelo.
- **Sincronização** idempotente: reenviar o mesmo atendimento atualiza, não duplica.
- **Grafo de sintomas** (Neo4j) e dados geográficos (PostgreSQL/PostGIS).
- **Auditoria** de ações sensíveis (login, uso do assistente de IA etc.).
- **Transcrição de áudio** (Whisper) e **assistente Gemini** (opcional).

## Arquitetura

```
 Navegador (index.html, Cloudflare Pages)
        │  HTTPS (ngrok com domínio fixo)
        ▼
 FastAPI ──► PostgreSQL + PostGIS   (atendimentos, usuários, auditoria)
        └──► Neo4j                  (grafo de sintomas)
        └──► Gemini (opcional)      (resumo/descrição, só rascunho)
```

Tudo roda em contêineres Docker (API, PostgreSQL/PostGIS, Neo4j).

## Modelo de risco de gravidade

- **Dados:** SINAN/Dengue, arquivo DENGBR23 (1.426.773 notificações).
- **Alvo:** gravidade (classificação final dengue com sinais de alarme/grave versus dengue clássica).
- **Divisão temporal** 70/15/15 (treino 998.741; validação 214.016; teste 214.016).
- **Modelo oficial:** regressão logística, com a mesma matemática em Python (`app/services/ia_risco.py`) e em JavaScript (`site/index.html`), para funcionar offline.
- **Limiar** escolhido para ~80% de sensibilidade.

Resultados no conjunto de teste (versão `risco-gravidade-lr-20261009-dias`):

| Métrica | Valor |
|---|---|
| ROC-AUC | 0,6782 (IC95% 0,6703–0,6859) |
| Sensibilidade | 79,8% |
| Especificidade | 41,9% |
| Precisão | 3,3% (taxa-base 2,4%) |
| Brier | 0,0235 |

Com o campo "dias desde o início dos sintomas", o ROC-AUC subiu de 0,651 para 0,678 (experimento em `ml/comparar_dias_sintomas.py`). Um modelo de árvores usado como referência chegou a 0,703; a regressão logística foi mantida por ser interpretável e rodar no aparelho.

**Limitações:** a discriminação é modesta; a acurácia (42,8%) é baixa porque o limiar foi fixado para alta sensibilidade; a base aberta não traz casos descartados (só prováveis); o modelo não detecta dengue; não houve validação em campo. Relatório completo em `ml/relatorio_metricas.md`.

## Assistente de IA (Gemini)

- Opcional: sem `GEMINI_API_KEY`, fica desligado e o restante funciona normalmente.
- Gera **somente rascunho**; o médico decide.
- **LGPD:** só vão para o Google idade, sexo, sintomas, sinais de alarme, nível/pontuação e o relato (com CPF, CNS, telefone e e-mail removidos). Foto exige confirmação de ciência do profissional.
- Cada uso é registrado na auditoria (`USAR_ASSISTENTE_IA`).
- Precisa de internet; o ACS offline não é afetado.
- Descrições de foto **não têm validação clínica**.

## Como rodar

Pré-requisitos: Docker Desktop (Windows). Copie `.env.example` para `.env` e preencha as senhas e a `SECRET_KEY`. **Nunca** envie o `.env` ao GitHub.

```powershell
.\ligar.ps1      # sobe API, PostgreSQL/PostGIS, Neo4j e o túnel
.\desligar.ps1   # para tudo (os dados ficam guardados)
```

Endereço fixo do túnel (opcional): coloque o domínio do ngrok em `tunel.txt` (uma linha). O arquivo não vai para o GitHub.

A documentação interativa da API fica em `/docs` (Swagger).

## Testes

```powershell
docker compose exec api python -m pytest -q
```

A suíte tem **253 testes** (autenticação, atendimentos, sincronização, modelo de risco, dias desde os sintomas, transcrição e assistente de IA).

## Retreinar o modelo

```powershell
python -m venv .venv-ml
.\.venv-ml\Scripts\Activate.ps1
pip install -r requirements-ml.txt
python ml\treinar_risco.py --dias-sintomas   # veja --help para os argumentos
```

O CSV do SINAN **não** é versionado. O resultado vai para `app/ia/modelo_risco.json`, que o front-end carrega para funcionar offline.

## Estrutura

```
app/            API FastAPI (routers, services, models, schemas)
app/ia/         modelo_risco.json (modelo de gravidade)
ml/             treino, métricas e experimentos
site/           front-end (index.html) publicado no Cloudflare Pages
scripts/        utilitários e scripts de atualização
tests/          testes automatizados
ligar.ps1 / desligar.ps1   sobe e para o ambiente
```

## Segurança

- Senhas, `SECRET_KEY` e chaves ficam só no `.env` (fora do Git).
- Em `AMBIENTE=producao` a API recusa subir com senha ou chave fraca.
- Acesso por perfil (ACS, Médico, Gestor) e trilha de auditoria.

## Trabalhos futuros

Modelos em grafo (GCN/GAT), IA embarcada no aparelho (Edge AI), validação em campo e migração para hospedagem em nuvem.
