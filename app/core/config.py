import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

# Endereços de telas de desenvolvimento que podem chamar a API pelo navegador.
# 5500 = Live Server do VS Code ou "python -m http.server 5500"; 3000 e 5173 = React/Vite; 8080 = servidor simples.
ORIGENS_PADRAO = (
    "http://localhost:5500,http://127.0.0.1:5500,"
    "http://localhost:3000,http://localhost:5173,http://localhost:8080"
)


CHAVES_PADRAO = {"dev-only-change-me", "secret", "changeme", "troque_por_uma_chave_longa_e_aleatoria"}


class Settings(BaseSettings):
    """Configurações lidas das variáveis de ambiente (.env)."""

    DATABASE_URL: str = "postgresql+psycopg2://medgraph:medgraph@localhost:5432/medgraph"

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "neo4j"

    # "desenvolvimento" (padrão) só avisa sobre configuração fraca; "producao" RECUSA subir com ela.
    AMBIENTE: str = "desenvolvimento"

    SECRET_KEY: str = "dev-only-change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Lista separada por vírgulas dos endereços (origens) autorizados a chamar a API pelo navegador.
    CORS_ORIGINS: str = ORIGENS_PADRAO

    # Pasta onde ficam o áudio do relato e a foto do exantema (dados de saúde: nunca pública).
    UPLOAD_DIR: str = "/code/uploads"
    MAX_AUDIO_MB: int = 15
    MAX_FOTO_MB: int = 8

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def em_producao(self) -> bool:
        return self.AMBIENTE.strip().lower() in ("producao", "produção", "production", "prod")

    def problemas_de_seguranca(self) -> list[str]:
        """Lista o que está fraco na configuração (vazia = tudo certo)."""
        problemas = []
        chave = self.SECRET_KEY
        if chave in CHAVES_PADRAO or any(t in chave.lower() for t in ("troque", "change-me", "changeme", "exemplo")):
            problemas.append("SECRET_KEY é um valor de exemplo/padrão. Gere uma: python -c \"import secrets; print(secrets.token_hex(32))\"")
        elif len(chave) < 32:
            problemas.append("SECRET_KEY tem menos de 32 caracteres.")
        if self.NEO4J_PASSWORD in ("neo4j", "") or "troque" in self.NEO4J_PASSWORD.lower():
            problemas.append("NEO4J_PASSWORD é a senha padrão ou de exemplo.")
        if "medgraph:medgraph@" in self.DATABASE_URL or "troque" in self.DATABASE_URL.lower():
            problemas.append("A senha do PostgreSQL (DATABASE_URL) é a padrão ou de exemplo.")
        if self.ACCESS_TOKEN_EXPIRE_MINUTES > 480:
            problemas.append("ACCESS_TOKEN_EXPIRE_MINUTES maior que 480 (8 h) deixa o token vivo por tempo demais.")
        if any("*" in o for o in self.cors_origins_list):
            problemas.append("CORS_ORIGINS não pode conter curinga (*).")
        if self.em_producao:
            for o in self.cors_origins_list:
                if o.startswith("http://") and "localhost" not in o and "127.0.0.1" not in o:
                    problemas.append(f"CORS_ORIGINS tem origem sem HTTPS em produção: {o}")
        return problemas

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()


def exigir_config_segura(cfg: Settings | None = None) -> list[str]:
    """Em produção, recusa subir com configuração fraca. Em desenvolvimento, só avisa no log."""
    cfg = cfg or settings
    problemas = cfg.problemas_de_seguranca()
    if not problemas:
        return problemas
    if cfg.em_producao:
        raise RuntimeError(
            "Configuração insegura para produção:\n- " + "\n- ".join(problemas)
            + "\nCorrija o arquivo .env e suba de novo."
        )
    for p in problemas:
        logging.getLogger("medgraph.config").warning("Config fraca (ok só em desenvolvimento): %s", p)
    return problemas
