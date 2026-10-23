"""Bounded document extraction in a disposable subprocess; no uploaded files are saved."""

import io
import sys
import zipfile
from defusedxml import ElementTree as ET


def main():
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
        if sys.platform.startswith("linux"):
            resource.setrlimit(
                resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024)
            )
    except (ImportError, ValueError):
        pass
    data = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("File exceeds 2 MB.")
    ext = sys.argv[1]
    if ext in (".txt", ".md", ".json"):
        text = data.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError("Binary files are not supported as text.")
    elif ext == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries = z.infolist()
            if (
                len(entries) > 500
                or sum(i.file_size for i in entries) > 10 * 1024 * 1024
            ):
                raise ValueError("This Word document is too large when expanded.")
            xml = z.read("word/document.xml")
            root = ET.fromstring(
                xml, forbid_dtd=True, forbid_entities=True, forbid_external=True
            )
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            text = "\n\n".join(
                "".join(p.itertext()) for p in root.findall(".//w:p", ns)
            )
    elif ext == ".pdf":
        from pypdf import PdfReader

        if not data.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF file.")
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or len(reader.pages) > 50:
            raise ValueError("Use an unencrypted PDF with at most 50 pages.")
        parts = []
        count = 0
        for page in reader.pages:
            part = page.extract_text() or ""
            count += len(part)
            if count > 100000:
                raise ValueError("Document text exceeds 100,000 characters.")
            parts.append(part)
        text = "\n\n".join(parts)
    else:
        raise ValueError("Unsupported document type.")
    if not text.strip():
        raise ValueError(
            "No selectable text found. Upload TXT, Markdown or a text-based document."
        )
    if len(text) > 100000:
        raise ValueError("Document text exceeds 100,000 characters.")
    sys.stdout.write(text)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stdout.write(
            str(exc)[:180]
            if isinstance(exc, (ValueError, UnicodeError))
            else "Could not read this file. Try TXT or Markdown."
        )
        raise SystemExit(1)
