# app/services/neo4j_service.py
from typing import List, Optional
from neo4j import GraphDatabase
from app.core.config import settings

class Neo4jService:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        )

    def close(self):
        self.driver.close()

    def sincronizar_atendimento_grafo(
        self,
        paciente_id: str,
        sintomas: List[str],
        codigo_ibge: str,
        localidade_nome: str,
        foco_vetor_id: Optional[str] = None,
        tipo_criadouro: Optional[str] = None
    ):
        """
        Cria/atualiza a estrutura de nós e arestas no Neo4j conforme a RN04 (Rastreabilidade no Grafo).
        Nenhum nó/relação é removido fisicamente; apenas mesclado ou atualizado.
        """
        query = """
        // 1. Garante Nó de Paciente e Localidade
        MERGE (p:NoPaciente {pacienteId: $paciente_id})
        MERGE (l:NoLocalidade {codigoIBGE: $codigo_ibge})
        ON CREATE SET l.nome = $localidade_nome
        
        // Relacionamento RESIDE_EM
        MERGE (p)-[:RESIDE_EM]->(l)

        // 2. Garante Nós de Sintomas e Relacionamentos APRESENTA
        WITH p, l
        UNWIND $sintomas AS sintoma_nome
        MERGE (s:NoSintoma {nomeSintoma: sintoma_nome})
        MERGE (p)-[:APRESENTA]->(s)

        // 3. Processa Foco de Vetor (se informado)
        WITH l
        FOREACH (_ IN CASE WHEN $foco_vetor_id IS NOT NULL THEN [1] ELSE [] END |
            MERGE (f:NoFocoVetor {id: $foco_vetor_id})
            ON CREATE SET f.tipoCriadouro = $tipo_criadouro, f.dataIdentificacao = datetime()
            MERGE (f)-[:LOCALIZADO_EM]->(l)
        )
        """
        with self.driver.session() as session:
            session.run(
                query,
                paciente_id=paciente_id,
                sintomas=sintomas,
                codigo_ibge=codigo_ibge,
                localidade_nome=localidade_nome,
                foco_vetor_id=foco_vetor_id,
                tipo_criadouro=tipo_criadouro
            )

neo4j_service = Neo4jService()