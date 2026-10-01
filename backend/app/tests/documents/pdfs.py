"""PDFs built in the tests, so a fixture says what each text draws: visible, invisible, tiny."""


def text(
    words: str,
    *,
    y: int = 700,
    size: float = 12,
    mode: int = 0,
    transparent: bool = False,
    clear_outline: bool = False,
) -> str:
    """One line of Helvetica. ``mode`` is the text render mode: 1 draws the outline only, 2 fill
    and outline, 3 nothing, 7 only clips. ``transparent`` clears the fill, ``clear_outline`` the
    outline."""
    state = ("/Clear gs " if transparent else "") + ("/ClearOutline gs " if clear_outline else "")
    return f"q {state}BT /F1 {size} Tf {mode} Tr 72 {y} Td ({words}) Tj ET Q\n"


def pdf(*pages: str) -> bytes:
    """A PDF with one page per content stream."""
    count = len(pages)
    font = 3 + 2 * count
    clear = font + 1
    clear_outline = font + 2
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids ["
        + " ".join(f"{3 + 2 * index} 0 R" for index in range(count))
        + f"] /Count {count} >>",
    ]
    for index, content in enumerate(pages):
        stream = content.encode("latin-1")
        objects.append(
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font} 0 R >> "
            f"/ExtGState << /Clear {clear} 0 R /ClearOutline {clear_outline} 0 R >> >> "
            f"/Contents {4 + 2 * index} 0 R >>"
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n{stream.decode('latin-1')}\nendstream"
        )
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append("<< /Type /ExtGState /ca 0 >>")
    objects.append("<< /Type /ExtGState /CA 0 >>")
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def form(value: str) -> bytes:
    """A one-page PDF whose only content is a form's text field holding ``value``: it has no text
    layer, and a viewer paints the value."""
    objects = [
        "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R] /NeedAppearances true "
        "/DA (/F1 12 Tf 0 g) /DR << /Font << /F1 4 0 R >> >> >> >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Annots [5 0 R] "
        "/Resources << /Font << /F1 4 0 R >> >> >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Type /Annot /Subtype /Widget /FT /Tx /T (field) /V ({value}) "
        "/Rect [72 700 400 720] /F 4 /P 3 0 R /DA (/F1 12 Tf 0 g) >>",
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)
