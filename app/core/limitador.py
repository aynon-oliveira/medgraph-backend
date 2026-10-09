"""Limite de tentativas de login (passo 28).

O endereco do servidor e publico (tunel), entao alguem poderia tentar milhares de senhas. Regra simples:
depois de LOGIN_MAX_FALHAS erros seguidos para o MESMO e-mail dentro de LOGIN_JANELA_SEG, aquele e-mail fica
bloqueado por LOGIN_BLOQUEIO_SEG. Um login certo zera a contagem.

- A chave e o e-mail digitado (existindo ou nao), entao nao revela quais e-mails sao cadastrados.
- O IP nao serve de chave: atras do tunel todos chegam com o IP do proxy.
- Fica na memoria do processo (some ao reiniciar). Para a demonstracao isso basta.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque


def _inteiro(nome: str, padrao: int) -> int:
    try:
        return max(1, int(os.environ.get(nome) or padrao))
    except ValueError:
        return padrao


def max_falhas() -> int:
    return _inteiro("LOGIN_MAX_FALHAS", 5)


def janela_seg() -> int:
    return _inteiro("LOGIN_JANELA_SEG", 900)


def bloqueio_seg() -> int:
    return _inteiro("LOGIN_BLOQUEIO_SEG", 300)


MAX_CHAVES = 10000  # evita crescer sem limite se alguem inventar e-mails sem parar


class LimitadorLogin:
    def __init__(self, relogio=time.monotonic):
        self._relogio = relogio
        self._falhas: dict[str, deque] = {}
        self._bloqueado_ate: dict[str, float] = {}
        self._trava = threading.Lock()

    @staticmethod
    def chave(email: str) -> str:
        return (email or "").strip().lower()[:150]

    def segundos_bloqueado(self, chave: str) -> int:
        """0 se pode tentar; senao, quantos segundos faltam."""
        agora = self._relogio()
        with self._trava:
            ate = self._bloqueado_ate.get(chave)
            if ate is None:
                return 0
            if agora >= ate:
                del self._bloqueado_ate[chave]
                return 0
            return int(ate - agora) + 1

    def registrar_falha(self, chave: str) -> None:
        agora = self._relogio()
        with self._trava:
            fila = self._falhas.setdefault(chave, deque())
            while fila and agora - fila[0] > janela_seg():
                fila.popleft()
            fila.append(agora)
            if len(fila) >= max_falhas():
                self._bloqueado_ate[chave] = agora + bloqueio_seg()
                del self._falhas[chave]
            self._podar(agora)

    def registrar_sucesso(self, chave: str) -> None:
        with self._trava:
            self._falhas.pop(chave, None)
            self._bloqueado_ate.pop(chave, None)

    def zerar(self) -> None:
        with self._trava:
            self._falhas.clear()
            self._bloqueado_ate.clear()

    def _podar(self, agora: float) -> None:
        if len(self._falhas) + len(self._bloqueado_ate) <= MAX_CHAVES:
            return
        for k in [k for k, ate in self._bloqueado_ate.items() if agora >= ate]:
            del self._bloqueado_ate[k]
        for k in [k for k, f in self._falhas.items() if not f or agora - f[-1] > janela_seg()]:
            del self._falhas[k]
        while len(self._falhas) > MAX_CHAVES:  # ainda grande: descarta as mais antigas
            self._falhas.pop(next(iter(self._falhas)))


limitador_login = LimitadorLogin()
