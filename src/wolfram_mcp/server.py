from __future__ import annotations
import argparse
import json as _json
import os
import logging
from pathlib import Path
from typing import List, Optional, Any
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import FastMCP
from .wolfram import WolframEngine
from .lsp_client import WolframLSPClient

app = FastMCP("wolfram-mcp")
logger = logging.getLogger("wolfram_mcp.server")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
engine: WolframEngine | None = None
_lsp_client: WolframLSPClient | None = None
_project_root: str | None = None

# Resource helpers
_LATEST_NOTEBOOK_PATH: str | None = None
# In‑memory registry for generic rendered images (hash -> metadata)
_IMAGE_REGISTRY: dict[str, dict[str, Any]] = {}


def _get_lsp_client() -> WolframLSPClient:
    global _lsp_client
    if _lsp_client is None:
        assert engine is not None, "Engine not initialized"
        assert engine._kernel_path is not None, (
            "No Wolfram Kernel found. Set WOLFRAM_KERNEL_PATH or --kernel-path."
        )
        root = _project_root or os.getcwd()
        _lsp_client = WolframLSPClient(kernel_path=engine._kernel_path, project_root=root)
    return _lsp_client


def _resolve_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    root = _project_root or os.getcwd()
    return os.path.join(root, path)


def _uri_to_relative(uri: str) -> str:
    parsed = urlparse(uri)
    abs_path = unquote(parsed.path)
    root = _project_root or os.getcwd()
    try:
        return os.path.relpath(abs_path, root)
    except ValueError:
        return abs_path

@app.tool()
def evaluate(code: str) -> str:
    """Evaluate a Wolfram Language expression."""
    assert engine is not None, "Engine not initialized"
    return engine.evaluate(code)

@app.tool()
def render_image(
    code: str,
    fmt: str = "PNG",
    width: int | None = None,
    height: int | None = None,
    dpi: int | None = None,
    auto_register: bool = True,
    include_hash_preview: bool = False,
):  # type: ignore[override]
    """Render a Wolfram Language expression as an image and emit MCP multimodal content.

    Behavior summary (current contract):
    - Always writes the rasterized image to a deterministic file (under WOLFRAM_MCP_IMAGE_DIR or ./.wolfram_images).
    - Returns a list: [text_block, image_block]. The image block contains: type, format, width, height, exprType,
      success, error (possibly None), and uri (file://...). No base64 payload is ever returned.
    - Optional registration (register=True) records metadata in an in‑memory registry keyed by a 32‑hex digest
      of (code, fmt, width, height, dpi). A resource URI (wolfram://image/byhash/<digest>) is added to the image block.
    - include_hash_preview=True adds digestPrefix (first 8 hex) for quick human reference.

    Parameters:
      code: WL source expression (Graphics/Image/Plot/etc.).
      fmt: Export format (PNG/JPEG/etc.).
      width/height/dpi: Optional size & resolution hints passed through to rasterization.
      auto_register: If True, store metadata and add resource field.
      include_hash_preview: If True and registered, include digestPrefix in image block.

    Returns: list[dict]
      [text_block, image_block]. image_block never includes a 'data' key (URI‑only policy).
    """
    assert engine is not None, "Engine not initialized"
    import json as _json
    import hashlib
    from pathlib import Path

    raster_json = engine.evaluate_raster(code, fmt=fmt, width=width, height=height, dpi=dpi)
    data = _json.loads(raster_json)
    summary = f"Rendered {code[:60]}{'...' if len(code)>60 else ''} => {data.get('format', fmt)} {data.get('width')}x{data.get('height')}"

    img_format = data.get("format", fmt)
    b64_data: str | None = data.get("data")  # Will be discarded; we no longer return base64
    uri: str | None = None

    # Pre-compute full digest for potential registration or deterministic filename.
    h_full = hashlib.sha256()
    for part in [code, img_format, str(width), str(height), str(dpi)]:
        h_full.update(str(part).encode("utf-8"))
    digest_full = h_full.hexdigest()
    digest16 = digest_full[:16]
    digest32 = digest_full[:32]
    resource_uri: str | None = None

    # If a uri is requested, persist image bytes to disk.
    if b64_data:
        # Directory
        from pathlib import Path
        out_dir = Path(os.environ.get("WOLFRAM_MCP_IMAGE_DIR", ".wolfram_images"))
        out_dir.mkdir(parents=True, exist_ok=True)
        # Deterministic hash
        ext = img_format.lower()
        filename = f"img_{digest16}_{data.get('width')}x{data.get('height')}.{ext}"
        file_path = out_dir / filename
        if not file_path.exists():
            try:
                import base64
                raw_bytes = base64.b64decode(b64_data)
                file_path.write_bytes(raw_bytes)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed writing image file %s: %s", file_path, e)
            else:
                logger.info("Wrote image file %s", file_path)
        uri = file_path.resolve().as_uri()

        # If mode == uri we purposely drop the base64 payload to shrink tokens.
        # Discard base64 to enforce URI-only contract
        b64_data = None

    image_block: dict[str, object] = {
        "type": "image",
        "format": img_format,
        "width": data.get("width"),
        "height": data.get("height"),
        "exprType": data.get("exprType"),
        "success": data.get("success"),
        "error": data.get("error"),
    }
    if uri is not None:
        image_block["uri"] = uri

    # Optional registration/upgrade
    if auto_register:
        resource_uri = f"wolfram://image/byhash/{digest32}"
        existing = _IMAGE_REGISTRY.get(digest32)
        has_data_now = False  # base64 disabled
        if existing:
            pass  # No upgrade path needed without base64
        else:
            meta = {
                "code": code,
                "fmt": img_format,
                "width": data.get("width"),
                "height": data.get("height"),
                "dpi": dpi,
                "resource": resource_uri,
                "uri": uri,
                "hasData": False,
                "mode": "uri",
            }
            _IMAGE_REGISTRY[digest32] = meta
        image_block["resource"] = resource_uri
        if include_hash_preview:
            image_block["digestPrefix"] = digest32[:8]

    text_block = {"type": "text", "text": summary}

    blocks = [text_block, image_block]
    if auto_register and resource_uri:
        # Add an explicit guidance block to nudge client usage of resource URI.
        guidance = {
            "type": "text",
            "role": "system",
            "text": (
                "Image registered. Prefer referring to it via its stable resource URI "
                f"{resource_uri} rather than re-uploading. You can fetch metadata with the resource "
                "and avoid duplicate storage."
            ),
            "registered": True,
        }
        blocks.append(guidance)
    return blocks

# --- Helper tool for autonomous image referencing (attach existing file) ---
@app.tool()
def attach_saved_image(path: str):
    """Attach an existing local image file as an image block (URI only) plus a follow-up prompt.

    Base64 embedding has been removed for consistency with the URI‑only policy. The returned
    list contains: [text_block, image_block, analysis_request].
    """
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"No such file: {path}")
    ext = p.suffix.lower().lstrip('.') or 'png'
    if ext not in {"png", "jpg", "jpeg", "gif", "webp"}:
        ext = "png"
    uri = p.resolve().as_uri()
    image_block: dict[str, object] = {"type": "image", "format": ext.upper(), "uri": uri}
    text_block = {"type": "text", "text": f"Attached image from {p} ({ext.upper()})."}
    analysis_block = {"type": "text", "text": "Please describe the image content succinctly."}
    return [text_block, image_block, analysis_block]


@app.tool()
def list_image_resources() -> List[str]:
    """List registered generic image resource URIs."""
    return [meta["resource"] for meta in _IMAGE_REGISTRY.values()]

@app.tool()
def create_notebook(path: str, cells: List[str] | None = None) -> str:
    """Create a new notebook (.nb) file with optional initial cell contents."""
    assert engine is not None, "Engine not initialized"
    global _LATEST_NOTEBOOK_PATH
    result = engine.create_notebook(path, cells)
    _LATEST_NOTEBOOK_PATH = result
    return result

@app.tool()
def append_cell(path: str, cell: str, style: str = "Input") -> str:
    """Append a single cell (with optional style) to the notebook and return new cell count."""
    assert engine is not None, "Engine not initialized"
    global _LATEST_NOTEBOOK_PATH
    _LATEST_NOTEBOOK_PATH = path
    return engine.append_cell(path, cell, style)

@app.tool()
def append_cells(path: str, contents: List[str], styles: Optional[List[str]] = None) -> str:
    """Append multiple cells.

    Parameters:
      path: Notebook file path.
      contents: List of cell contents (strings).
      styles: Optional list of styles (same length). Defaults to "Input" for all if omitted.
    Returns: New total cell count as string.
    """
    assert engine is not None, "Engine not initialized"
    if styles is None:
        styles = ["Input"] * len(contents)
    if len(styles) != len(contents):
        raise ValueError("styles length must match contents length")
    global _LATEST_NOTEBOOK_PATH
    _LATEST_NOTEBOOK_PATH = path
    pairs = list(zip(contents, styles))
    return engine.append_cells(path, pairs)

@app.tool()
def append_cells_json(path: str, cells_json: str) -> str:
    """Append multiple cells provided as a JSON string.

    cells_json schema examples:
      1. [{"content": "Title text", "style": "Title"}, {"content": "Some text", "style": "Text"}]
      2. [["Title text", "Title"], ["Some text", "Text"], ["2+2", "Input"]]
      3. ["Simple input cell", "Another input cell"]  (defaults style=Input)
    Returns new total cell count.
    """
    assert engine is not None, "Engine not initialized"
    import json as _json
    try:
        raw = _json.loads(cells_json)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Invalid JSON: {e}") from e
    pairs: List[tuple[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                content = str(item.get("content", ""))
                style = str(item.get("style", "Input")) or "Input"
                pairs.append((content, style))
            elif isinstance(item, list) and len(item) in (1, 2):
                content = str(item[0])
                style = str(item[1]) if len(item) == 2 else "Input"
                pairs.append((content, style or "Input"))
            elif isinstance(item, str):
                pairs.append((item, "Input"))
            else:
                raise ValueError(f"Unsupported cell entry format: {item!r}")
    else:
        raise ValueError("cells_json must decode to a JSON array")
    global _LATEST_NOTEBOOK_PATH
    _LATEST_NOTEBOOK_PATH = path
    return engine.append_cells(path, pairs)

@app.tool()
def replace_cell(path: str, index: int, cell: str) -> str:
    """Replace cell at (1-based) index and return the replaced cell as string."""
    assert engine is not None, "Engine not initialized"
    global _LATEST_NOTEBOOK_PATH
    _LATEST_NOTEBOOK_PATH = path
    return engine.replace_cell(path, index, cell)

@app.tool()
def list_cells(path: str) -> List[str]:
    """List cells (string form)."""
    assert engine is not None, "Engine not initialized"
    return engine.list_cells(path)

@app.tool()
def get_cell(path: str, index: int) -> str:
    """Get a single notebook cell (string form) by 1-based index."""
    assert engine is not None, "Engine not initialized"
    return engine.get_cell(path, index)

@app.tool()
def search_notebook(path: str, query: str, ignore_case: bool = True, max_results: int = 50) -> str:
    """Search a notebook's cells for a substring. Returns JSON with indices & content of matches."""
    assert engine is not None, "Engine not initialized"
    return engine.search_notebook(path, query, ignore_case, max_results)

@app.tool()
def export_notebook(path: str, format: str = "Plaintext") -> str:
    """Export notebook to a format (e.g., Plaintext, JSON)."""
    assert engine is not None, "Engine not initialized"
    return engine.export_notebook(path, format)

@app.tool()
def ping() -> str:
    """Health check returning a simple string."""
    return "pong"

@app.tool()
def create_function_doc(symbol: str, out_dir: str = "docs/symbols") -> str:
    """Create a documentation notebook skeleton for a symbol."""
    assert engine is not None, "Engine not initialized"
    return engine.create_function_documentation(symbol, out_dir)

@app.tool()
def create_paclet_doc_skeleton(paclet_name: str, out_dir: str = "docs/paclets") -> str:
    """Scaffold paclet documentation directory structure and guide notebook."""
    assert engine is not None, "Engine not initialized"
    return engine.create_paclet_doc_skeleton(paclet_name, out_dir)

@app.tool()
def frontend_notebook_example(path: str = "example_frontend.nb") -> str:
    """Create and edit a notebook via front-end NotebookWrite/SelectionMove returning JSON summary."""
    assert engine is not None, "Engine not initialized"
    global _LATEST_NOTEBOOK_PATH
    res = engine.frontend_notebook_example(path)
    _LATEST_NOTEBOOK_PATH = path
    return res


# Resources
@app.resource("wolfram://doc/symbol/template", title="Symbol Documentation Skeleton", description="Return a JSON skeleton template for symbol documentation page")
def resource_symbol_doc():  # type: ignore[override]
    return {
        "template": {
            "titleCell": "Documentation for <Symbol>",
            "sections": ["Usage", "Details", "Examples", "See Also"],
            "placeholders": {"<Symbol>": "Replace with actual symbol name"}
        }
    }

@app.resource("wolfram://notebook/latest", title="Latest Notebook Path", description="Shows the last notebook path touched by notebook tools.")
def resource_latest_notebook():  # type: ignore[override]
    return {"path": _LATEST_NOTEBOOK_PATH}

@app.resource("wolfram://doc/symbol/{symbol}", title="Symbol Documentation Page", description="Materialize a symbol documentation page skeleton with the given symbol name.")
def resource_symbol_instance(symbol: str):  # type: ignore[override]
    return {
        "symbol": symbol,
        "cells": [
            {"style": "Title", "content": f"{symbol}"},
            {"style": "Text", "content": f"Usage for {symbol}"},
            {"style": "Code", "content": f"?{symbol}"},
            {"style": "Section", "content": "Examples"},
            {"style": "Input", "content": f"{symbol}[args]"},
            {"style": "Section", "content": "See Also"}
        ]
    }

@app.resource("wolfram://image/byhash/{digest}", title="Registered Rendered Image", description="Fetch metadata for a previously registered rendered image.")
def resource_image_byhash(digest: str):  # type: ignore[override]
    meta = _IMAGE_REGISTRY.get(digest)
    if not meta:
        return {"error": "Unknown image hash", "digest": digest}
    # Do not automatically embed base64 here (keeps resource lightweight). Client can decide to re-render with richer mode.
    return meta



# --- LSP Code Intelligence Tools ---

@app.tool()
def document_symbols(path: str, depth: int = 0) -> str:
    """Get symbols defined in a Wolfram Language file (.wl/.wls).

    Returns JSON list of symbols with names, kinds, and ranges.
    Use this to understand the structure of a Wolfram source file.

    Parameters:
      path: Path to the .wl or .wls file (absolute or relative to project root).
      depth: How deep to show nested symbols (0 = top-level only).
    """
    client = _get_lsp_client()
    symbols = client.document_symbols(_resolve_path(path))

    def truncate(sym: dict, d: int) -> dict:
        result = {k: v for k, v in sym.items() if k != "children"}
        if d > 0 and "children" in sym:
            result["children"] = [truncate(c, d - 1) for c in sym["children"]]
        return result

    symbols = [truncate(s, depth) for s in symbols]
    return _json.dumps(symbols, indent=2)


@app.tool()
def hover_info(path: str, line: int, character: int) -> str:
    """Get documentation/type info for a symbol at a position in a Wolfram file.

    Parameters:
      path: Path to the file (absolute or relative to project root).
      line: 0-based line number.
      character: 0-based column number.
    """
    client = _get_lsp_client()
    result = client.hover(_resolve_path(path), line, character)
    if result is None:
        return "No hover information available at this position."
    contents = result.get("contents", "")
    if isinstance(contents, dict):
        return contents.get("value", str(contents))
    if isinstance(contents, list):
        parts = []
        for item in contents:
            if isinstance(item, dict):
                parts.append(item.get("value", str(item)))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(contents)


@app.tool()
def find_definition(path: str, line: int, character: int) -> str:
    """Go to definition of the symbol at a position.

    Parameters:
      path: Path to the file containing the symbol reference.
      line: 0-based line number.
      character: 0-based column number.
    Returns: JSON list of definition locations with file paths and ranges.
    """
    client = _get_lsp_client()
    locations = client.definition(_resolve_path(path), line, character)
    for loc in locations:
        if "uri" in loc:
            loc["path"] = _uri_to_relative(loc["uri"])
        if "targetUri" in loc:
            loc["targetPath"] = _uri_to_relative(loc["targetUri"])
    return _json.dumps(locations, indent=2) if locations else "No definition found."


@app.tool()
def find_references(path: str, line: int, character: int) -> str:
    """Find all references to the symbol at a position across the project.

    Parameters:
      path: Path to the file containing the symbol.
      line: 0-based line number.
      character: 0-based column number.
    Returns: JSON list of reference locations.
    """
    client = _get_lsp_client()
    refs = client.references(_resolve_path(path), line, character)
    for ref in refs:
        if "uri" in ref:
            ref["path"] = _uri_to_relative(ref["uri"])
    return _json.dumps(refs, indent=2) if refs else "No references found."


@app.tool()
def get_diagnostics(path: str = "") -> str:
    """Get diagnostic messages (errors, warnings) from the Wolfram LSP.

    Parameters:
      path: Path to a specific file (absolute or relative), or empty for all diagnostics.
    Returns: JSON object mapping file paths to their diagnostics.
    """
    client = _get_lsp_client()
    if path:
        uri = Path(_resolve_path(path)).as_uri()
        diags = client.get_diagnostics(uri)
    else:
        diags = client.get_diagnostics()
    result = {}
    for uri, items in diags.items():
        result[_uri_to_relative(uri)] = items
    return _json.dumps(result, indent=2) if result else "No diagnostics available."


@app.tool()
def list_project_files(directory: str = "") -> str:
    """List Wolfram Language source files (.wl, .wls, .m, .nb) in the project.

    Parameters:
      directory: Subdirectory to list (relative to project root). Empty = entire project.
    Returns: JSON list of file paths relative to project root.
    """
    root = _project_root or os.getcwd()
    search_root = os.path.join(root, directory) if directory else root
    extensions = {".wl", ".wls", ".m", ".nb"}
    files = []
    for dirpath, dirnames, filenames in os.walk(search_root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for f in sorted(filenames):
            if os.path.splitext(f)[1] in extensions:
                abs_path = os.path.join(dirpath, f)
                files.append(os.path.relpath(abs_path, root))
    return _json.dumps(files, indent=2)


@app.tool()
def read_source_file(path: str, start_line: int = 0, end_line: int = -1) -> str:
    """Read a source file from the project.

    Parameters:
      path: Path to the file (absolute or relative to project root).
      start_line: First line to read (0-based). Default: 0.
      end_line: Last line to read (exclusive, 0-based). -1 = entire file.
    Returns: File contents (or the requested line range).
    """
    abs_path = _resolve_path(path)
    text = Path(abs_path).read_text(encoding="utf-8")
    if start_line == 0 and end_line == -1:
        return text
    lines = text.splitlines(keepends=True)
    if end_line == -1:
        end_line = len(lines)
    return "".join(lines[start_line:end_line])


def run(argv: List[str] | None = None) -> None:
    global engine, _project_root
    parser = argparse.ArgumentParser(description="Wolfram MCP Server")
    parser.add_argument("--kernel-path", help="Path to Wolfram Kernel executable", default=None)
    parser.add_argument("--project-root", help="Root directory for Wolfram Language project (for code intelligence)", default=None)
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio", help="MCP transport mode")
    parser.add_argument("--quiet", action="store_true", help="Reduce logging output (same as --log-level WARNING)")
    parser.add_argument("--log-level", default=None, choices=["DEBUG","INFO","WARNING","ERROR","CRITICAL"], help="Explicit log level override")
    args = parser.parse_args(argv)

    _project_root = args.project_root

    # Determine log level
    if args.log_level:
        level = getattr(logging, args.log_level)
        logging.getLogger().setLevel(level)
    elif args.quiet:
        logging.getLogger().setLevel(logging.WARNING)
    # De-noise underlying library loggers that can emit "Processing request" at WARNING
    for noisy in ["mcp.server.lowlevel", "mcp.server.fastmcp", "anyio"]:
        logging.getLogger(noisy).setLevel(logging.ERROR)

    logger.info("Starting Wolfram MCP server (transport=%s)", args.transport)
    kernel_path = args.kernel_path or os.environ.get("WOLFRAM_KERNEL_PATH")
    engine = WolframEngine(kernel_path)
    try:
        app.run(transport=args.transport)
    except KeyboardInterrupt:  # Graceful shutdown, no traceback spam
        logger.info("Received KeyboardInterrupt, shutting down.")
    finally:
        if _lsp_client is not None:
            _lsp_client.stop()
        if engine is not None:
            engine.close()
        logger.info("Server stopped.")
        # Ensure clean exit code
        return 0

if __name__ == "__main__":  # pragma: no cover
    run()
