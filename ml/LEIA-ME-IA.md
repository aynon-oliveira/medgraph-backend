# Passo 21b — Modelo de risco de gravidade na dengue (dados reais do SINAN)

## O que mudou e por quê
O arquivo aberto do SINAN (dengue 2023) traz só **casos prováveis**: não tem casos descartados (`CLASSI_FIN` = 5).
Sem esse grupo, não dá para treinar "tem dengue × não tem dengue". O modelo agora responde outra pergunta,
que também serve à triagem:

> Entre pessoas com dengue notificada, qual a **chance de evoluir com sinais de alarme ou dengue grave**?

- **Positivo:** `CLASSI_FIN` 11 (dengue com sinais de alarme) ou 12 (dengue grave).
- **Negativo:** `CLASSI_FIN` 10 (dengue sem sinais de alarme).
- **Variáveis (conhecidas na visita):** 12 sintomas, idade, sexo, gestante e 7 condições de risco
  (diabetes, doença hematológica, hepatopatia, doença renal, hipertensão, doença ácido-péptica, doença autoimune).
- **Fora do modelo:** os próprios sinais de alarme/gravidade (são o desfecho), leucopenia e prova do laço (dependem de exame).
- **Limiar:** o maior que ainda acha 80% dos casos graves na validação (em triagem, é melhor errar para o lado seguro).
  Por isso muita gente fica acima do limiar: a especificidade é baixa. Isso vai no artigo.
- **Divisão:** no tempo, 70% mais antigos treino, 15% validação, 15% mais recentes teste (usado uma única vez).
- Todos os critérios de encerramento entram (laboratorial e clínico-epidemiológico), porque o desfecho vem do quadro clínico.

O modelo **não diz se a pessoa tem dengue**. Quem tem sinal de alarme marcado continua sendo classificado como alto risco (RN02).

## Rodar (PowerShell, pasta `medgraph-backend`, com `(.venv-ml)` ativo)

    python ml/treinar_risco.py --csv "C:\Users\HYDRO\Downloads\DENGBR23.csv\DENGBR23.csv"

Tudo em UMA linha. Leva alguns minutos (arquivo de 463 MB). Se faltar memória, acrescente `--max-linhas 500000`.

Opcional: `--alvo dengue` (dengue × descartado) só funciona com arquivo que tenha `CLASSI_FIN` = 5.

## Saídas
- `app/ia/modelo_risco.json` — o modelo (coeficientes, limiar, taxa base, métricas e dados usados).
- `ml/relatorio_metricas.md`, `.json` e `.png` — os números que podem ir para o artigo (ROC-AUC com IC 95%,
  sensibilidade, especificidade, matriz de confusão, calibração por decis, razões de chances).

## Limitações que o artigo deve declarar
- Só casos notificados e investigados; o atendimento em campo pode ser diferente.
- A base não tem descartados: o modelo não detecta dengue, só estima gravidade entre casos de dengue.
- O desfecho pode aparecer dias depois da visita; o modelo usa só o que se sabe na visita.
- Sem validação em campo nem em aparelhos reais. É apoio à decisão (RN01), nunca diagnóstico.

## Testes (Docker)

    docker compose exec api pytest tests/test_ia_risco.py -v
