"""Legacy binary Office files (.doc, .xls, .ppt): converted once by LibreOffice to DOCX, XLSX or PPTX for pandoc."""

from __future__ import annotations

from pathlib import Path

from crepe_mcp.exporter import find_libreoffice, office_convert
from crepe_mcp.reader.common import LEGACY_OFFICE, ReaderError

CONVERT_TIMEOUT = 180


def convert_legacy(source: Path, work: Path) -> Path:
    """The OOXML copy of a legacy Office file, written into the folder `work` under the same name stem."""
    target = LEGACY_OFFICE[source.suffix.lower()]
    command = find_libreoffice()
    if command is None:
        raise ReaderError(
            f"Reading a {source.suffix} file needs LibreOffice to convert it, and it was not found. Install it or "
            f"set CREPE_LIBREOFFICE_PATH; or save the file as .{target}."
        )
    try:
        return Path(office_convert(command, str(source), str(work), target, timeout=CONVERT_TIMEOUT))
    except RuntimeError as error:
        raise ReaderError(str(error)) from error
