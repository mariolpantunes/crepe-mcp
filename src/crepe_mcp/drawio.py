"""draw.io diagram export utilities for the CREPE MCP server.

Provides two MCP-facing functions:
  export_drawio  — run draw.io headlessly to export a diagram file.
  inspect_drawio — parse a .drawio file offline and list its pages.

Also exports two helpers used by linter.py:
  try_decode_compressed — base64+deflate decode for compressed diagram XML.
  read_drawio_bytes     — read raw XML from a plain or zip-compressed .drawio file.

Environment variables:
  CREPE_DRAWIO_PATH — path to the draw.io binary (native installs only).
      If unset, the server auto-detects the binary on PATH, the macOS app
      bundle, or the Flatpak com.jgraph.drawio.desktop package.
      Flatpak installs do not need this variable set.
"""
from __future__ import annotations

import base64
import io
import os
import shutil
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
import zlib

# ---------------------------------------------------------------------------
# Shared helpers (also imported by linter.py — canonical home)
# ---------------------------------------------------------------------------

def try_decode_compressed(text: str) -> str | None:
    """Try to base64-decode and zlib-decompress draw.io diagram text content.

    draw.io compresses diagram content as:
        base64(deflate(encodeURIComponent(xml)))
    Returns the decoded XML string on success, None if the text is not
    compressed data or decoding fails for any reason.
    """
    try:
        decoded_bytes = base64.b64decode(text)
        try:
            xml_bytes = zlib.decompress(decoded_bytes, -15)  # raw deflate
        except zlib.error:
            xml_bytes = zlib.decompress(decoded_bytes)       # standard zlib
        return urllib.parse.unquote(xml_bytes.decode("utf-8"))
    except Exception:
        return None


def read_drawio_bytes(path: str) -> bytes:
    """Read raw XML bytes from a .drawio file (plain XML or zip-compressed).

    Raises OSError / zipfile.BadZipFile / ValueError on failure.
    """
    with open(path, "rb") as f:
        header = f.read(4)
        f.seek(0)
        raw = f.read()

    # ZIP magic bytes: PK\x03\x04
    if header[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
            xml_names = [n for n in names if n.endswith(".xml") or n.endswith(".drawio")]
            if not xml_names:
                xml_names = names[:1] if names else []
            if not xml_names:
                raise ValueError(
                    f"No XML entry found in zip archive. Contents: {names}"
                )
            return zf.read(xml_names[0])

    return raw


# ---------------------------------------------------------------------------
# Internal: draw.io discovery
# ---------------------------------------------------------------------------

def find_drawio() -> list[str] | None:
    """Return the command prefix to invoke draw.io headlessly, or None.

    Priority:
      1. CREPE_DRAWIO_PATH env var (must be an executable file).
      2. 'drawio' or 'draw.io' on PATH.
      3. macOS app bundle.
      4. Flatpak com.jgraph.drawio.desktop (Linux only).

    Returns None in two distinct cases:
      - CREPE_DRAWIO_PATH is set but the path is not a valid executable
        (callers should surface this explicitly — see _drawio_not_found_error).
      - draw.io is simply not installed anywhere.
    Use _drawio_not_found_error() to generate the appropriate error message.
    """
    override = os.environ.get("CREPE_DRAWIO_PATH", "").strip()
    if override:
        if "com.jgraph.drawio.desktop" in override or "flatpak" in override:
            if shutil.which("flatpak"):
                try:
                    res = subprocess.run(
                        ["flatpak", "info", "com.jgraph.drawio.desktop"],
                        capture_output=True, timeout=5,
                    )
                    if res.returncode == 0:
                        return ["flatpak", "run", "--filesystem=host", "--filesystem=/tmp", "com.jgraph.drawio.desktop"]
                except Exception:
                    pass
            return None
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return [override]
        # Configured but not valid — don't silently fall through.
        return None

    for binary in ("drawio", "draw.io"):
        found = shutil.which(binary)
        if found:
            return [found]

    macos_path = "/Applications/draw.io.app/Contents/MacOS/draw.io"
    if os.path.isfile(macos_path) and os.access(macos_path, os.X_OK):
        return [macos_path]

    if sys.platform.startswith("linux") and shutil.which("flatpak"):
        try:
            result = subprocess.run(
                ["flatpak", "info", "com.jgraph.drawio.desktop"],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                return [
                    "flatpak", "run",
                    "--filesystem=host", "--filesystem=/tmp",
                    "com.jgraph.drawio.desktop",
                ]
        except Exception:
            pass

    return None


def _drawio_not_found_error() -> str:
    """Return a human-readable error string explaining why draw.io was not found."""
    override = os.environ.get("CREPE_DRAWIO_PATH", "").strip()
    if override:
        if not os.path.isfile(override):
            return (
                f"CREPE_DRAWIO_PATH is set to {override!r} but that path does not exist. "
                "Update the variable to point at the draw.io executable."
            )
        if not os.access(override, os.X_OK):
            return (
                f"CREPE_DRAWIO_PATH is set to {override!r} but the file is not executable. "
                "Run: chmod +x " + override
            )
    return (
        "draw.io not found. Install it natively (e.g. via your package manager "
        "or https://github.com/jgraph/drawio-desktop/releases), via the macOS "
        "app bundle, or via Flatpak: "
        "`flatpak install flathub com.jgraph.drawio.desktop`. "
        "Optionally set CREPE_DRAWIO_PATH to the binary path."
    )


def _display_env() -> dict[str, str]:
    """Return an env dict with a best-effort DISPLAY/WAYLAND_DISPLAY set.

    draw.io (Electron) needs a display even for headless export. If neither
    DISPLAY nor WAYLAND_DISPLAY is already set in the environment, fall back
    to ':0' so the call does not fail immediately on a bare server. The
    Flatpak sandbox provides its own virtual display and ignores this.
    """
    env = os.environ.copy()
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        env["DISPLAY"] = ":0"
    return env


# ---------------------------------------------------------------------------
# Public: export
# ---------------------------------------------------------------------------

_VALID_FORMATS = {"pdf", "png", "jpg", "svg", "xml", "html"}


def export_drawio(
    input_path: str,
    output_path: str,
    output_format: str = "png",
    page_index: int = 1,
    all_pages: bool = False,
    transparent: bool = True,
    scale: float = 1.0,
    border: int = 0,
    embed_diagram: bool = False,
    timeout: int = 60,
) -> dict:
    """Export a .drawio/.xml file using draw.io headlessly.

    Returns {"success": True, "output_path": ..., "size_bytes": ...} or
    {"success": False, "error": ...}.
    """
    # --- Validate inputs ---
    if output_format not in _VALID_FORMATS:
        return {
            "success": False,
            "error": f"output_format must be one of {sorted(_VALID_FORMATS)}, got {output_format!r}",
        }
    if not os.path.isabs(input_path):
        return {"success": False, "error": f"input_path must be absolute, got {input_path!r}"}
    if not os.path.isabs(output_path):
        return {"success": False, "error": f"output_path must be absolute, got {output_path!r}"}
    if not os.path.isfile(input_path):
        return {"success": False, "error": f"input_path not found: {input_path!r}"}

    if not all_pages and page_index < 1:
        return {"success": False, "error": f"page_index must be >= 1, got {page_index}"}

    cmd_prefix = find_drawio()
    if cmd_prefix is None:
        return {"success": False, "error": _drawio_not_found_error()}

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    cmd = cmd_prefix + [
        "--export",
        "--format", output_format,
        "--output", output_path,
        "--border", str(border),
    ]
    if all_pages:
        cmd.append("--all-pages")
    else:
        cmd += ["--page-index", str(page_index)]
    if transparent:
        cmd.append("--transparent")
    if scale != 1.0:
        cmd += ["--scale", str(scale)]
    if embed_diagram:
        cmd.append("--embed-diagram")
    cmd.append(input_path)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_display_env(),
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"draw.io timed out after {timeout}s"}
    except Exception as exc:
        return {"success": False, "error": f"Failed to launch draw.io: {exc}"}

    if result.returncode != 0:
        stderr = result.stderr.strip()
        return {
            "success": False,
            "error": f"draw.io export failed (exit {result.returncode}):\n{stderr}",
        }

    if not os.path.isfile(output_path):
        return {"success": False, "error": f"draw.io did not produce the expected output: {output_path!r}"}

    return {
        "success": True,
        "output_path": output_path,
        "output_format": output_format,
        "size_bytes": os.path.getsize(output_path),
    }


# ---------------------------------------------------------------------------
# Public: list pages and structural XML validation
# ---------------------------------------------------------------------------

def _check_graph_model(model_elem: ET.Element, page_desc: str, warnings: list[str]) -> None:
    root_elem = model_elem.find("root")
    if root_elem is None:
        warnings.append(f"{page_desc}: Missing <root> element inside <mxGraphModel>.")
        return

    cells = root_elem.findall("mxCell")
    cell_ids: set[str] = set()
    has_root_0 = False
    has_root_1 = False

    for cell in cells:
        cid = cell.get("id")
        if cid is None:
            warnings.append(f"{page_desc}: Found <mxCell> missing 'id' attribute.")
            continue
        if cid in cell_ids:
            warnings.append(f"{page_desc}: Duplicate mxCell id='{cid}'.")
        cell_ids.add(cid)

        if cid == "0":
            has_root_0 = True
        elif cid == "1":
            has_root_1 = True

    if not has_root_0:
        warnings.append(f"{page_desc}: Missing base root cell <mxCell id=\"0\"/>.")
    if not has_root_1:
        warnings.append(f"{page_desc}: Missing default layer cell <mxCell id=\"1\" parent=\"0\"/>.")


def inspect_drawio(input_path: str) -> dict:
    """Validate a .drawio file and return its pages and structural diagnostics without invoking draw.io.

    Parses the XML (plain or zip-compressed) and validates the root structure.
    Returns page names, indices, structural validity flag, and warnings.
    """
    if not os.path.isabs(input_path):
        return {"success": False, "error": f"input_path must be absolute, got {input_path!r}"}
    if not os.path.isfile(input_path):
        return {"success": False, "error": f"File not found: {input_path!r}"}

    try:
        raw_bytes = read_drawio_bytes(input_path)
    except Exception as exc:
        return {"success": False, "error": f"Could not read file: {exc}"}

    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as exc:
        return {"success": False, "error": f"XML parse error: {exc}"}

    warnings: list[str] = []
    pages: list[dict] = []

    if root.tag == "mxfile":
        diagrams = root.findall("diagram")
        if not diagrams:
            warnings.append("The <mxfile> root element contains no <diagram> children.")

        for i, diagram in enumerate(diagrams, start=1):
            name = diagram.get("name", f"Page-{i}")
            pages.append({"index": i, "name": name})

            if len(diagram):
                graph_model = diagram.find("mxGraphModel")
                if graph_model is not None:
                    _check_graph_model(graph_model, f"Page {i} ({name})", warnings)
            elif diagram.text and diagram.text.strip():
                text = diagram.text.strip()
                # Try base64+deflate decompression first (draw.io web app format)
                decoded = try_decode_compressed(text)
                if decoded is not None:
                    text = decoded
                if text.startswith("<"):
                    try:
                        inner_root = ET.fromstring(text)
                        if inner_root.tag == "mxGraphModel":
                            _check_graph_model(inner_root, f"Page {i} ({name})", warnings)
                    except ET.ParseError:
                        pass
    elif root.tag == "mxGraphModel":
        pages.append({"index": 1, "name": "Page-1"})
        _check_graph_model(root, "Page 1", warnings)
    else:
        return {
            "success": False,
            "error": (
                f"Unexpected root element <{root.tag}>; "
                "expected <mxfile> or <mxGraphModel>. "
                "Is this a valid .drawio file?"
            ),
        }

    if not pages:
        return {"success": False, "error": "The file contains no diagram pages."}

    return {
        "success": True,
        "input_path": input_path,
        "page_count": len(pages),
        "pages": pages,
        "is_valid_structure": len(warnings) == 0,
        "warnings": warnings,
    }
