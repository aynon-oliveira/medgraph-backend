"""Corrige o teste test_padrao_e_desenvolvimento: ele lia o AMBIENTE=producao do container.
Uso, na pasta medgraph-backend:  python scripts/corrigir_teste_ambiente.py
"""
import pathlib
import sys

caminho = pathlib.Path(__file__).resolve().parent.parent / "tests" / "test_config_segura.py"
bruto = caminho.read_bytes().decode("utf-8")
crlf = "\r\n" in bruto
texto = bruto.replace("\r\n", "\n")
if "monkeypatch.delenv" in texto:
    print("Ja estava corrigido, pulei.")
    sys.exit(0)
antigo = "def test_padrao_e_desenvolvimento():\n    assert Settings(_env_file=None).em_producao is False\n"
novo = (
    "def test_padrao_e_desenvolvimento(monkeypatch):\n"
    "    monkeypatch.delenv(\"AMBIENTE\", raising=False)  # o container real roda com AMBIENTE=producao\n"
    "    assert Settings(_env_file=None).em_producao is False\n"
)
if texto.count(antigo) != 1:
    sys.exit("ERRO: nao achei o trecho esperado em tests/test_config_segura.py. Me mande uma foto desta mensagem.")
texto = texto.replace(antigo, novo)
caminho.write_bytes((texto.replace("\n", "\r\n") if crlf else texto).encode("utf-8"))
print("tests/test_config_segura.py: corrigido.")
