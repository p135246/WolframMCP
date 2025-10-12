from __future__ import annotations
import argparse
import os
import logging
from typing import List, Optional, Any

from mcp.server.fastmcp import FastMCP
from .wolfram import WolframEngine

app = FastMCP("wolfram-mcp")
logger = logging.getLogger("wolfram_mcp.server")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
engine: WolframEngine | None = None

# Resource helpers
_LATEST_NOTEBOOK_PATH: str | None = None
# In‑memory registry for generic rendered images (hash -> metadata)
_IMAGE_REGISTRY: dict[str, dict[str, Any]] = {}

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
    register: bool = True,
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
      register: If True, store metadata and add resource field.
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
    if register:
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
    if register and resource_uri:
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



def run(argv: List[str] | None = None) -> None:
    global engine
    parser = argparse.ArgumentParser(description="Wolfram MCP Server")
    parser.add_argument("--kernel-path", help="Path to Wolfram Kernel executable", default=None)
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio", help="MCP transport mode")
    parser.add_argument("--quiet", action="store_true", help="Reduce logging output (same as --log-level WARNING)")
    parser.add_argument("--log-level", default=None, choices=["DEBUG","INFO","WARNING","ERROR","CRITICAL"], help="Explicit log level override")
    args = parser.parse_args(argv)

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
        if engine is not None:
            engine.close()
        logger.info("Server stopped.")
        # Ensure clean exit code
        return 0

if __name__ == "__main__":  # pragma: no cover
    run()
