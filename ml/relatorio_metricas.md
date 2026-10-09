# Relatório de métricas do classificador de risco de gravidade na dengue

Gerado em 2026-10-09T14:02:06+00:00.

**Alvo:** evolucao com sinais de alarme ou dengue grave (CLASSI_FIN 11/12) x dengue sem sinais de alarme (CLASSI_FIN 10), entre casos provaveis de dengue notificados - todos os criterios.

## Dados

- Arquivo: `DENGBR23.csv` (SHA-256 `ab350800e8f4109d…`)
- Taxa do desfecho nos dados usados: 1.78%
- Linhas lidas: 1,645,956 | casos usados: 1,426,773 (positivos 25,354; negativos 1,401,419)
- Divisão temporal: treino 998,741, validação 214,016, teste 214,016

## Resultado no conjunto de teste (usado uma única vez)

| Modelo | ROC-AUC | F1 | Sensibilidade | Especificidade | Acurácia |
| --- | --- | --- | --- | --- | --- |
| Regressão logística (exportado) | 0.6782 | 0.0635 | 0.7978 | 0.4188 | 0.4281 |
| Árvores (referência) | 0.7032 | 0.125 | 0.1661 | 0.9628 | 0.9435 |

IC 95% (bootstrap) da regressão logística: ROC-AUC [0.6703, 0.6859]; F1 [0.0616, 0.0654]. Limiar escolhido na validação: 0.0109.

Matriz de confusão (teste): {'vn': 87460, 'fp': 121353, 'fn': 1052, 'vp': 4151}

## Razões de chances (quanto cada variável aumenta a chance do desfecho)

| Variável | Razão de chances |
| --- | --- |
| Hipertensão arterial | 1.967 |
| Petéquias | 1.766 |
| Vômito | 1.737 |
| Diabetes | 1.476 |
| Dias desde o início dos sintomas | 1.373 |
| Náusea | 1.339 |
| Dor intensa nas articulações | 1.246 |
| Manchas na pele (exantema) | 1.211 |
| Doença renal crônica | 1.202 |
| Febre | 1.159 |
| Idade (anos) | 1.134 |
| Doença autoimune | 1.132 |
| Gestante | 1.112 |
| Doença hematológica | 1.082 |
| Hepatopatia | 1.051 |
| Dor muscular | 1.042 |
| Conjuntivite | 1.039 |
| Dor atrás dos olhos | 1.02 |
| Doença ácido-péptica | 0.946 |
| Sexo feminino | 0.905 |
| Artrite | 0.653 |
| Dor de cabeça | 0.641 |
| Dor nas costas | 0.64 |

## Calibração (teste)

| Faixa prevista | n | previsto | observado |
| --- | --- | --- | --- |
| 0.002-0.006 | 21402 | 0.0053 | 0.0095 |
| 0.006-0.008 | 21402 | 0.0072 | 0.0112 |
| 0.008-0.009 | 21402 | 0.0085 | 0.0115 |
| 0.009-0.011 | 21402 | 0.0099 | 0.0149 |
| 0.011-0.012 | 21402 | 0.0115 | 0.017 |
| 0.012-0.015 | 21402 | 0.0135 | 0.0199 |
| 0.015-0.018 | 21401 | 0.0161 | 0.0236 |
| 0.018-0.022 | 21401 | 0.0198 | 0.0304 |
| 0.022-0.031 | 21401 | 0.0261 | 0.0375 |
| 0.031-0.531 | 21401 | 0.0501 | 0.0677 |
