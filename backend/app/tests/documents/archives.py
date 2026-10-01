"""Archives built in the tests, so a fixture says what it holds. rar has no free writer, so a RAR5
archive is written here from its format (stored, not compressed); a winmail.dat likewise from
TNEF's."""

import io
import struct
import tarfile
import zipfile
import zlib

import libarchive


def zip_of(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def zip_named_in(codepage: str, name: str, data: bytes) -> bytes:
    """A zip whose one member's name is written in a DOS code page without the UTF-8 flag, as
    Windows' own zip tool writes it."""
    raw = name.encode(codepage)
    placeholder = "x" * len(raw)
    return zip_of({placeholder: data}).replace(placeholder.encode(), raw)


def encrypted_zip(name: str, data: bytes) -> bytes:
    """A zip whose member is marked encrypted (general purpose bit 0), as a password sets it."""
    archive = bytearray(zip_of({name: data}))
    local = archive.find(b"PK\x03\x04")
    central = archive.find(b"PK\x01\x02")
    archive[local + 6] |= 1
    archive[central + 8] |= 1
    return bytes(archive)


def seven_zip_of(members: dict[str, bytes]) -> bytes:
    buffer = bytearray()

    def write(data: bytes) -> int:
        buffer.extend(data)
        return len(data)

    with libarchive.custom_writer(write, "7zip") as archive:
        for name, data in members.items():
            archive.add_file_from_memory(name, len(data), data)
    return bytes(buffer)


def tar_gz_of(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _vint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _rar_block(kind: int, flags: int, fields: bytes, data_size: int | None = None) -> bytes:
    body = _vint(kind) + _vint(flags)
    if data_size is not None:
        body += _vint(data_size)
    body += fields
    header = _vint(len(body)) + body
    return struct.pack("<I", zlib.crc32(header)) + header


def rar5_of(members: dict[str, bytes]) -> bytes:
    """A RAR5 archive of stored files: signature, main header, a file header and its data per
    member, end of archive."""
    out = b"Rar!\x1a\x07\x01\x00" + _rar_block(1, 0, _vint(0))
    for name, data in members.items():
        encoded = name.encode()
        fields = (
            _vint(0x0004)  # file flags: data CRC32 present
            + _vint(len(data))  # unpacked size
            + _vint(0x20)  # attributes: archive
            + struct.pack("<I", zlib.crc32(data))
            + _vint(0)  # compression: version 0, method 0 (store)
            + _vint(0)  # host OS: Windows
            + _vint(len(encoded))
            + encoded
        )
        out += _rar_block(2, 0x0002, fields, data_size=len(data)) + data
    return out + _rar_block(5, 0, _vint(0))


def rar5_with_encrypted_headers() -> bytes:
    """A RAR5 archive whose headers are encrypted (rar -hp): its encryption header comes first,
    so not even the names can be listed without the password."""
    fields = _vint(0) + _vint(0x0001) + bytes([15]) + b"s" * 16 + b"c" * 12  # AES-256, check
    return b"Rar!\x1a\x07\x01\x00" + _rar_block(4, 0, fields) + b"\x00" * 64


def _tnef_attribute(level: int, attribute: int, data: bytes) -> bytes:
    checksum = sum(data) & 0xFFFF
    return struct.pack("<BII", level, attribute, len(data)) + data + struct.pack("<H", checksum)


def _mapi_property(kind: int, name: int, value: bytes) -> bytes:
    """One MAPI property holding one value of a counted type (a string or bytes), as TNEF packs
    them: the value's length, then the value padded to four bytes."""
    padded = value + b"\x00" * (-len(value) % 4)
    return struct.pack("<I", 1) + struct.pack("<HHII", kind, name, 1, len(value)) + padded


def stored_rtf(rtf: bytes) -> bytes:
    """Rich text as a mail's compressed-RTF property holds it when it is not compressed: its
    sizes, the mark "MELA" and no checksum."""
    return struct.pack("<IIII", len(rtf) + 12, len(rtf), 0x414C454D, 0) + rtf


def winmail_of(
    attachments: dict[str, bytes],
    *,
    rtf_body: bytes | None = None,
    html_body: bytes | None = None,
    content_ids: dict[str, str] | None = None,
) -> bytes:
    """A winmail.dat: TNEF's signature and key, its version, the body as compressed rich text or
    as HTML when it has one, then per attachment its rendering record (which starts it), title,
    data and, for a picture the body draws, its Content-ID. Text is in the file's code page."""
    out = struct.pack("<IH", 0x223E9F78, 0x0001)
    out += _tnef_attribute(1, 0x00089006, struct.pack("<I", 0x00010000))
    if content_ids or html_body is not None:
        # The code page text properties are written in, as Outlook names it: a Turkish Windows.
        out += _tnef_attribute(1, 0x00069007, struct.pack("<II", 1254, 0))
    if rtf_body is not None:
        out += _tnef_attribute(1, 0x00069003, _mapi_property(0x0102, 0x1009, rtf_body))
    if html_body is not None:
        out += _tnef_attribute(1, 0x00069003, _mapi_property(0x0102, 0x1013, html_body))
    for name, data in attachments.items():
        out += _tnef_attribute(2, 0x00069002, b"\x00" * 14)
        out += _tnef_attribute(2, 0x00018010, name.encode("cp1252") + b"\x00")
        out += _tnef_attribute(2, 0x0006800F, data)
        if content_ids and name in content_ids:
            cid = content_ids[name].encode() + b"\x00"
            out += _tnef_attribute(2, 0x00069005, _mapi_property(0x001E, 0x3712, cid))
    return out
