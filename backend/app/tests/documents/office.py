"""Office files built by hand for the tests: the few parts LibreOffice needs, nothing more."""

import io
import zipfile
from html import escape

_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
    'relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/></Types>'
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
)


def docx_of(*paragraphs: str, hidden: str | None = None) -> bytes:
    """A Word file with these paragraphs, and ``hidden`` in a run marked hidden (``w:vanish``)."""
    runs = [f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>" for text in paragraphs]
    if hidden is not None:
        runs.append(f"<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>{escape(hidden)}</w:t></w:r></w:p>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(runs)}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", _TYPES)
        docx.writestr("_rels/.rels", _RELS)
        docx.writestr("word/document.xml", document)
    return buffer.getvalue()


def rtf_of(text: str) -> bytes:
    return ("{\\rtf1\\ansi{\\fonttbl\\f0 Arial;}\\f0 " + text + "\\par}").encode()
