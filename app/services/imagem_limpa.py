"""Remove metadados de fotos (EXIF, XMP, comentarios...) sem recodificar a imagem (passo 28).

Fotos de celular costumam trazer a localizacao GPS, o modelo do aparelho e a data. Isso nao pode ficar guardado
nem ir para o Gemini. Aqui se apagam esses blocos diretamente nos bytes: os pixels ficam IDENTICOS (sem perda de qualidade).

- JPEG: apaga APP1 (EXIF/XMP), APP3-APP13, APP15, comentarios, MPF e o que vier depois do fim da imagem. Mantem APP0
  (JFIF), APP2 (perfil de cor ICC) e APP14 (Adobe). Preserva so a orientacao (foto em pe/deitada) num EXIF minimo.
- PNG: apaga eXIf, tEXt, zTXt, iTXt e tIME.
- WebP: apaga EXIF e XMP.
- Se o arquivo nao tiver a forma esperada, devolve os MESMOS bytes (nunca quebra o upload).
"""
from __future__ import annotations

import struct

_ASSINATURA_PNG = b"\x89PNG\r\n\x1a\n"
_PNG_APAGAR = (b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME")


def remover_metadados(dados: bytes) -> bytes:
    try:
        if dados[:3] == b"\xff\xd8\xff":
            novo = _jpeg(dados)
        elif dados[:8] == _ASSINATURA_PNG:
            novo = _png(dados)
        elif dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
            novo = _webp(dados)
        else:
            return dados
    except Exception:  # noqa: BLE001  (qualquer surpresa: nao mexe no arquivo)
        return dados
    return dados if novo is None else novo


# ------------------------------------------------------------------ JPEG
def _orientacao(tiff: bytes) -> int | None:
    """Le a orientacao (1 a 8) do bloco TIFF do EXIF, se existir."""
    if len(tiff) < 8:
        return None
    if tiff[:2] == b"II":
        e = "<"
    elif tiff[:2] == b"MM":
        e = ">"
    else:
        return None
    if struct.unpack(e + "H", tiff[2:4])[0] != 42:
        return None
    off = struct.unpack(e + "I", tiff[4:8])[0]
    if off + 2 > len(tiff):
        return None
    qtd = struct.unpack(e + "H", tiff[off:off + 2])[0]
    for k in range(min(qtd, 64)):
        p = off + 2 + 12 * k
        if p + 12 > len(tiff):
            break
        tag, tipo, cont = struct.unpack(e + "HHI", tiff[p:p + 8])
        if tag == 0x0112 and tipo == 3 and cont == 1:
            v = struct.unpack(e + "H", tiff[p + 8:p + 10])[0]
            return v if 1 <= v <= 8 else None
    return None


def _exif_so_orientacao(o: int) -> bytes:
    tiff = (b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 1)
            + struct.pack(">HHI", 0x0112, 3, 1) + struct.pack(">HH", o, 0) + struct.pack(">I", 0))
    corpo = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(corpo) + 2) + corpo


def _apagar_segmento_jpeg(marcador: int, corpo: bytes) -> bool:
    if marcador == 0xE1:                       # EXIF e XMP
        return True
    if marcador == 0xE2:                       # APP2: apaga so o MPF (fotos multiplas); mantem o perfil ICC
        return corpo.startswith(b"MPF\x00")
    if 0xE3 <= marcador <= 0xED or marcador == 0xEF:   # APP3..APP13 e APP15 (IPTC, Photoshop, thumbnails...)
        return True
    return marcador == 0xFE                    # comentarios


def _jpeg(d: bytes) -> bytes | None:
    n = len(d)
    i = 2
    saida: list[bytes] = [b"\xff\xd8"]
    orient = None
    terminou = False
    achou_sos = False
    while i < n and not terminou:
        if d[i] != 0xFF:
            return None
        while i < n and d[i] == 0xFF:
            i += 1
        if i >= n:
            return None
        m = d[i]
        i += 1
        if m == 0xD9:
            saida.append(b"\xff\xd9")
            terminou = True
            break
        if m == 0x01 or 0xD0 <= m <= 0xD8:
            saida.append(bytes([0xFF, m]))
            continue
        if i + 2 > n:
            return None
        tam = int.from_bytes(d[i:i + 2], "big")
        if tam < 2 or i + tam > n:
            return None
        corpo = d[i + 2:i + tam]
        seg = b"\xff" + bytes([m]) + d[i:i + tam]
        i += tam
        if m != 0xDA:
            if m == 0xE1 and corpo.startswith(b"Exif\x00\x00") and orient is None:
                orient = _orientacao(corpo[6:])
            if not _apagar_segmento_jpeg(m, corpo):
                saida.append(seg)
            continue
        # SOS: copia o cabecalho e depois os dados comprimidos, ate o proximo marcador de verdade
        achou_sos = True
        saida.append(seg)
        j = i
        while j < n:
            if d[j] == 0xFF and j + 1 < n:
                b = d[j + 1]
                if b == 0xFF:
                    j += 1
                    continue
                if b == 0x00 or 0xD0 <= b <= 0xD7:
                    j += 2
                    continue
                saida.append(d[i:j])
                i = j
                break
            j += 1
        else:
            saida.append(d[i:n])       # sem marcador de fim: copia o resto
            i = n
            break
    if not achou_sos:
        return None
    if orient and orient != 1:
        pos = 2 if len(saida) > 1 and saida[1][:2] == b"\xff\xe0" else 1
        saida.insert(pos, _exif_so_orientacao(orient))
    return b"".join(saida)


# ------------------------------------------------------------------ PNG
def _png(d: bytes) -> bytes | None:
    n = len(d)
    i = 8
    saida = [d[:8]]
    fim = False
    while i + 12 <= n:
        tam = int.from_bytes(d[i:i + 4], "big")
        tipo = d[i + 4:i + 8]
        if not tipo.isalpha() or i + 12 + tam > n:
            return None
        pedaco = d[i:i + 12 + tam]
        i += 12 + tam
        if tipo not in _PNG_APAGAR:
            saida.append(pedaco)
        if tipo == b"IEND":
            fim = True
            break
    return b"".join(saida) if fim else None


# ------------------------------------------------------------------ WebP
def _webp(d: bytes) -> bytes | None:
    n = len(d)
    tam = int.from_bytes(d[4:8], "little")
    if tam < 4 or tam + 8 > n:
        return None
    fim = tam + 8
    i = 12
    saida = []
    while i + 8 <= fim:
        tipo = d[i:i + 4]
        cont = int.from_bytes(d[i + 4:i + 8], "little")
        total = 8 + cont + (cont & 1)
        if i + 8 + cont > fim:
            return None
        pedaco = d[i:min(i + total, fim)]
        i += total
        if tipo in (b"EXIF", b"XMP "):
            continue
        if tipo == b"VP8X" and cont >= 10:
            b = bytearray(pedaco)
            b[8] &= ~0x0C & 0xFF          # desliga os avisos de EXIF e XMP
            pedaco = bytes(b)
        saida.append(pedaco)
    corpo = b"WEBP" + b"".join(saida)
    return b"RIFF" + len(corpo).to_bytes(4, "little") + corpo
