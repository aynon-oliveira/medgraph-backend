"""Testes do passo 28 que NAO precisam de banco: limite de login e remocao de metadados das fotos.

    docker compose exec api pytest tests/test_passo28.py -v
"""
import struct

from app.core.limitador import LimitadorLogin, bloqueio_seg, max_falhas
from app.services.imagem_limpa import remover_metadados


# ---------------------------------------------------------------- limite de login
class Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_bloqueia_depois_de_varias_falhas_e_libera_com_o_tempo():
    r = Relogio()
    lim = LimitadorLogin(relogio=r)
    chave = lim.chave("  Alguem@Exemplo.com ")
    assert chave == "alguem@exemplo.com"
    for _ in range(max_falhas() - 1):
        lim.registrar_falha(chave)
        assert lim.segundos_bloqueado(chave) == 0
    lim.registrar_falha(chave)
    assert lim.segundos_bloqueado(chave) > 0
    r.t += bloqueio_seg() + 1
    assert lim.segundos_bloqueado(chave) == 0


def test_login_certo_zera_a_contagem():
    lim = LimitadorLogin(relogio=Relogio())
    for _ in range(max_falhas() - 1):
        lim.registrar_falha("a@b.com")
    lim.registrar_sucesso("a@b.com")
    for _ in range(max_falhas() - 1):
        lim.registrar_falha("a@b.com")
    assert lim.segundos_bloqueado("a@b.com") == 0


def test_bloqueio_de_um_email_nao_afeta_outro():
    lim = LimitadorLogin(relogio=Relogio())
    for _ in range(max_falhas()):
        lim.registrar_falha("x@b.com")
    assert lim.segundos_bloqueado("x@b.com") > 0
    assert lim.segundos_bloqueado("y@b.com") == 0


# ---------------------------------------------------------------- metadados das fotos
def _seg(marcador: int, corpo: bytes) -> bytes:
    return b"\xff" + bytes([marcador]) + struct.pack(">H", len(corpo) + 2) + corpo


def _exif(orientacao: int) -> bytes:
    tiff = (b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 2)
            + struct.pack("<HHI", 0x0112, 3, 1) + struct.pack("<HH", orientacao, 0)
            + struct.pack("<HHI", 0x8825, 4, 1) + struct.pack("<I", 0) + struct.pack("<I", 0)
            + b"GPSLatitude-3.119-GPSLongitude-60.021")
    return _seg(0xE1, b"Exif\x00\x00" + tiff)


PIXELS = b"\x12\x34\xff\x00\x56\xff\xd0\x78\x9a"   # FF00 e RSTn fazem parte dos dados, nao sao marcadores


def _jpeg_falso(orientacao=6):
    return (b"\xff\xd8" + _seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
            + _exif(orientacao) + _seg(0xFE, b"comentario-secreto") + _seg(0xE1, b"http://ns.adobe.com/xap/1.0/\x00XMP-secreto")
            + _seg(0xDB, b"\x00" + b"\x10" * 64) + _seg(0xDA, b"\x01\x01\x00\x00\x3f\x00")
            + PIXELS + b"\xff\xd9" + b"TRAILER-com-GPS")


def test_jpeg_perde_gps_xmp_comentario_e_lixo_no_fim():
    original = _jpeg_falso()
    assert b"GPSLatitude" in original
    limpo = remover_metadados(original)
    for segredo in (b"GPSLatitude", b"GPSLongitude", b"comentario-secreto", b"XMP-secreto", b"TRAILER"):
        assert segredo not in limpo
    assert limpo.startswith(b"\xff\xd8") and limpo.endswith(b"\xff\xd9")
    assert PIXELS in limpo                       # dados da imagem intactos
    assert b"JFIF" in limpo and b"\x10" * 64 in limpo   # cabecalho e tabela de quantizacao ficam


def test_jpeg_mantem_so_a_orientacao():
    limpo = remover_metadados(_jpeg_falso(orientacao=6))
    i = limpo.index(b"Exif\x00\x00")
    tiff = limpo[i + 6:i + 6 + 26]
    assert tiff[:2] == b"MM"
    assert struct.pack(">HHI", 0x0112, 3, 1) in tiff
    assert struct.pack(">HH", 6, 0) in tiff
    assert b"GPS" not in limpo


def test_arquivos_estranhos_voltam_iguais():
    # os "arquivos" curtos usados nos testes antigos e lixo qualquer nao podem ser alterados nem quebrar
    for dado in (b"\xff\xd8\xff\xe0" + b"\x00" * 64, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
                 b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64, b"MZ\x90\x00" + b"\x00" * 10, b"", b"\xff\xd8\xff"):
        assert remover_metadados(dado) == dado


def test_png_perde_texto_e_exif():
    def chunk(tipo, corpo):
        return struct.pack(">I", len(corpo)) + tipo + corpo + b"\x00\x00\x00\x00"
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", b"\x00" * 13) + chunk(b"tEXt", b"GPS\x00segredo")
           + chunk(b"eXIf", b"GPSLatitude") + chunk(b"IDAT", b"dados") + chunk(b"IEND", b""))
    limpo = remover_metadados(png)
    assert b"segredo" not in limpo and b"GPSLatitude" not in limpo and b"eXIf" not in limpo
    assert b"IHDR" in limpo and b"IDAT" in limpo and limpo.endswith(b"IEND" + b"\x00\x00\x00\x00")
