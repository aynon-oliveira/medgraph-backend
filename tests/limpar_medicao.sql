-- Remove tudo que o script medir_sincronizacao.py criou (atendimentos de medição, pacientes e ACS temporários).
-- Ordem importa por causa das chaves estrangeiras.
DELETE FROM resultados_inferencia WHERE atendimento_id IN (SELECT id FROM atendimentos WHERE relato_texto LIKE '[MEDICAO-%');
DELETE FROM atendimentos WHERE relato_texto LIKE '[MEDICAO-%';
DELETE FROM pacientes WHERE nome LIKE 'Medicao Paciente %' AND id NOT IN (SELECT paciente_id FROM atendimentos);
DELETE FROM usuarios WHERE email LIKE '%@medicao.medgraph' AND id NOT IN (SELECT agente_id FROM atendimentos);
