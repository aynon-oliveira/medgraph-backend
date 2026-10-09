"""Armazenamento seguro de áudio e foto (RF02).

Regras de segurança:
- o tipo do arquivo é decidido pelo CONTEÚDO (primeiros bytes), nunca pelo nome ou pelo Content-Type
  enviado pelo cliente;
- o nome gravado em disco é gerado pelo servidor (o nome original é ignorado);
- há limite de tamanho, lido em pedaços para não encher a memória;
- na leitura, o caminho é conferido para nunca sair da pasta de uploads.
"""
import secrets
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings
from app.services.imagem_limpa import remover_metadados

TAMANHO_PEDACO = 64 * 1024


def _tipo_foto(inicio: bytes) -> tuple[str, str] | None:
    if inicio.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if inicio.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if inicio[:4] == b"RIFF" and inicio[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def _tipo_audio(inicio: bytes) -> tuple[str, str] | None:
    if inicio.startswith(b"\x1a\x45\xdf\xa3"):  # contêiner Matroska/WebM (gravação do navegador)
        return ".webm", "audio/webm"
    if inicio.startswith(b"OggS"):
        return ".ogg", "audio/ogg"
    if inicio[:4] == b"RIFF" and inicio[8:12] == b"WAVE":
        return ".wav", "audio/wav"
    if inicio[4:8] == b"ftyp":  # MP4/M4A (gravação do Safari e do Android)
        return ".m4a", "audio/mp4"
    if inicio.startswith(b"ID3") or inicio[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3", "audio/mpeg"
    return None


DETECTORES = {"foto": _tipo_foto, "audio": _tipo_audio}
LIMITES_MB = {"foto": lambda: settings.MAX_FOTO_MB, "audio": lambda: settings.MAX_AUDIO_MB}
TIPOS_POR_EXTENSAO = {
    ".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".webm": "audio/webm", ".ogg": "audio/ogg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
}


def pasta_base() -> Path:
    base = Path(settings.UPLOAD_DIR).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


def detectar_tipo(categoria: str, inicio: bytes) -> tuple[str, str] | None:
    """Devolve (extensão, mime) pelo conteúdo, ou None se não for um formato aceito."""
    return DETECTORES[categoria](inicio)


def limpar_metadados_do_arquivo(destino: Path) -> None:
    """Regrava a foto sem EXIF/XMP/comentários (os pixels não mudam). Se algo falhar, o upload segue."""
    try:
        original = destino.read_bytes()
        limpo = remover_metadados(original)
        if limpo != original:
            destino.write_bytes(limpo)
    except OSError:
        pass


def salvar(categoria: str, atendimento_id: uuid.UUID, arquivo: UploadFile) -> str:
    """Grava o arquivo e devolve o caminho RELATIVO (é o que vai para o banco)."""
    limite = LIMITES_MB[categoria]() * 1024 * 1024
    pedaco = arquivo.file.read(TAMANHO_PEDACO)
    if not pedaco:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Arquivo vazio.")
    tipo = detectar_tipo(categoria, pedaco)
    if tipo is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Formato não aceito para " + ("foto (use JPEG, PNG ou WebP)." if categoria == "foto"
                                                  else "áudio (use WebM, OGG, WAV, M4A ou MP3)."),
        )
    extensao, _ = tipo

    destino_rel = f"{categoria}/{atendimento_id}-{secrets.token_hex(8)}{extensao}"
    destino = pasta_base() / destino_rel
    destino.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    try:
        with open(destino, "wb") as saida:
            while pedaco:
                total += len(pedaco)
                if total > limite:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Arquivo maior que o limite de {LIMITES_MB[categoria]()} MB.",
                    )
                saida.write(pedaco)
                pedaco = arquivo.file.read(TAMANHO_PEDACO)
    except Exception:
        destino.unlink(missing_ok=True)  # não deixa arquivo pela metade
        raise
    if categoria == "foto":
        limpar_metadados_do_arquivo(destino)  # passo 28: tira GPS, modelo do celular e data da foto
    return destino_rel


def resolver(caminho_rel: str | None) -> Path | None:
    """Transforma o caminho do banco em arquivo real, só se estiver dentro da pasta de uploads."""
    if not caminho_rel:
        return None
    base = pasta_base()
    alvo = (base / caminho_rel).resolve()
    if not alvo.is_relative_to(base) or not alvo.is_file():
        return None
    return alvo


def remover(caminho_rel: str | None) -> None:
    alvo = resolver(caminho_rel)
    if alvo is not None:
        alvo.unlink(missing_ok=True)


def tipo_do_arquivo(caminho: Path) -> str:
    return TIPOS_POR_EXTENSAO.get(caminho.suffix.lower(), "application/octet-stream")
