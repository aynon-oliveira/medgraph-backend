"""Transcrição do áudio do relato (Whisper, rodando no SERVIDOR).

Usa o `faster-whisper` (Whisper otimizado, roda só com CPU, sem placa de vídeo). O modelo é baixado na
primeira transcrição e fica guardado no volume `whisper_cache` do Docker.

Variáveis de ambiente (opcionais, no .env):
    WHISPER_ATIVO=0        desliga a transcrição (a rota responde 503)
    WHISPER_MODELO=base    tiny | base | small | medium  (maior = mais preciso e mais lento; 'base' é rápido e bom para frases curtas)
    WHISPER_PREAQUECER=1   carrega o modelo quando o servidor liga (a 1ª transcrição não espera); 0 desliga

Importante: a transcrição é um RASCUNHO para o profissional conferir. Nunca substitui o relato digitado
nem altera o risco calculado.
"""
from __future__ import annotations

import importlib.util
import os
import threading

_modelo = None
_trava = threading.Lock()


class TranscricaoIndisponivel(RuntimeError):
    """O pacote não está instalado, ou foi desligado no .env."""


class TranscricaoFalhou(RuntimeError):
    """O áudio não pôde ser lido/transcrito."""


def nome_do_modelo() -> str:
    return (os.environ.get("WHISPER_MODELO") or "base").strip() or "base"


def disponivel() -> bool:
    if (os.environ.get("WHISPER_ATIVO") or "1").strip().lower() in ("0", "false", "nao", "não"):
        return False
    return importlib.util.find_spec("faster_whisper") is not None


def _carregar():
    global _modelo
    with _trava:
        if _modelo is None:
            from faster_whisper import WhisperModel  # import tardio: a API sobe mesmo sem o pacote

            _modelo = WhisperModel(nome_do_modelo(), device="cpu", compute_type="int8", cpu_threads=max(1, (os.cpu_count() or 4)))
        return _modelo


def juntar_trechos(trechos) -> str:
    """Junta os trechos reconhecidos em um texto só, sem espaços sobrando."""
    return " ".join(t.strip() for t in (getattr(x, "text", x) for x in trechos) if t and t.strip()).strip()


def transcrever(caminho) -> dict:
    """Devolve {texto, idioma, modelo}. Levanta TranscricaoIndisponivel ou TranscricaoFalhou."""
    if not disponivel():
        raise TranscricaoIndisponivel("Transcrição desligada ou não instalada neste servidor.")
    try:
        modelo = _carregar()
        trechos, info = modelo.transcribe(str(caminho), language="pt", vad_filter=True, beam_size=1,
                                          without_timestamps=True, condition_on_previous_text=False)
        texto = juntar_trechos(trechos)
    except TranscricaoIndisponivel:
        raise
    except Exception as erro:  # noqa: BLE001  (áudio corrompido, formato não lido, falta de memória...)
        raise TranscricaoFalhou(str(erro)[:200]) from erro
    return {"texto": texto, "idioma": getattr(info, "language", "pt"), "modelo": "whisper-" + nome_do_modelo()}


def aquecer_em_segundo_plano() -> None:
    """Carrega (e, se preciso, baixa) o modelo assim que o servidor liga, sem travar a subida da API."""
    if (os.environ.get("WHISPER_PREAQUECER") or "1").strip().lower() in ("0", "false", "nao", "não"):
        return
    if not disponivel():
        return

    def tarefa():
        try:
            _carregar()
        except Exception:  # noqa: BLE001  (sem internet no 1º uso, falta de memória...: a 1ª transcrição tenta de novo)
            pass
    threading.Thread(target=tarefa, name="whisper-aquecer", daemon=True).start()


import sys as _sys  # noqa: E402

if "pytest" not in _sys.modules:        # nos testes não carrega o modelo
    aquecer_em_segundo_plano()
