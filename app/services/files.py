import re
import stat
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import uuid4
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring
from fastapi import HTTPException, UploadFile

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_ARCHIVE_SIZE = 50 * 1024 * 1024
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".zip": "application/zip",
}


def inspect_content(stream: BinaryIO, extension: str) -> str:
    stream.seek(0)
    signature = stream.read(8)
    stream.seek(0)
    if extension == ".pdf":
        # shortcut: signature checks do not scan malware; add a scanner before public deployment.
        if not re.match(rb"%PDF-[12]\.\d", signature):
            raise HTTPException(415, "File content is not PDF")
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 1024))
        if b"%%EOF" not in stream.read():
            raise HTTPException(415, "Incomplete PDF")
        stream.seek(0)
        return MIME_TYPES[extension]

    if not signature.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        raise HTTPException(415, "File content is not a ZIP/DOCX archive")
    try:
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(item.file_size for item in entries) > MAX_ARCHIVE_SIZE:
                raise HTTPException(415, "Archive exceeds 1000 entries or 50 MiB expanded size")
            names = [item.filename for item in entries]
            if len(names) != len(set(names)):
                raise HTTPException(415, "Duplicate archive entries are not allowed")
            expanded = 0
            for item in entries:
                path = PurePosixPath(item.filename.replace("\\", "/"))
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or ":" in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or item.flag_bits & 1
                ):
                    raise HTTPException(415, "Unsafe or encrypted archive entry")
                with archive.open(item) as member:
                    while chunk := member.read(64 * 1024):
                        expanded += len(chunk)
                        if expanded > MAX_ARCHIVE_SIZE:
                            raise HTTPException(415, "Archive expanded size exceeds 50 MiB")
            is_docx = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}.issubset(names)
            if extension == ".docx":
                if not is_docx:
                    raise HTTPException(415, "DOCX package structure is missing")
                for name in ("[Content_Types].xml", "word/document.xml", "_rels/.rels"):
                    if archive.getinfo(name).file_size > 5 * 1024 * 1024:
                        raise HTTPException(415, "DOCX XML part exceeds 5 MiB")
                types = fromstring(archive.read("[Content_Types].xml"))
                expected = (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document.main+xml"
                )
                if not any(
                    node.get("PartName") == "/word/document.xml"
                    and node.get("ContentType") == expected
                    for node in types
                ):
                    raise HTTPException(415, "Invalid DOCX content type")
                document = fromstring(archive.read("word/document.xml"))
                fromstring(archive.read("_rels/.rels"))
                if document.tag != (
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document"
                ) or any("vbaproject" in name.lower() for name in names):
                    raise HTTPException(415, "Invalid or macro-enabled DOCX")
            elif is_docx:
                raise HTTPException(415, "DOCX content must use the .docx extension")
    except (
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        zlib.error,
        EOFError,
        ParseError,
        DefusedXmlException,
        ValueError,
    ):
        raise HTTPException(415, "Invalid or unsupported ZIP/DOCX content") from None
    finally:
        stream.seek(0)
    return MIME_TYPES[extension]


def save_upload(upload: UploadFile, directory: Path) -> dict:
    original = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not original or len(original) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in original):
        raise HTTPException(422, "Invalid filename")
    extension = Path(original).suffix.lower()
    if extension not in MIME_TYPES:
        raise HTTPException(415, "Only PDF, DOCX and ZIP files are accepted")
    upload.file.seek(0, 2)
    size = upload.file.tell()
    upload.file.seek(0)
    if size == 0:
        raise HTTPException(422, "File is empty")
    if size > MAX_FILE_SIZE:
        raise HTTPException(413, "File exceeds 10 MiB (10485760 bytes)")
    detected_mime = inspect_content(upload.file, extension)
    declared_mime = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if declared_mime not in ("", "application/octet-stream", detected_mime):
        raise HTTPException(415, "Declared MIME type does not match file content")
    directory.mkdir(parents=True, exist_ok=True)
    stored = uuid4().hex + extension
    destination = directory / stored
    # Exclusive creation also prevents overwrites in the unlikely event of a UUID collision.
    created = False
    try:
        with destination.open("xb") as output:
            created = True
            while chunk := upload.file.read(64 * 1024):
                output.write(chunk)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return {
        "original_filename": original,
        "stored_filename": stored,
        "content_type": detected_mime,
        "file_size": size,
    }
