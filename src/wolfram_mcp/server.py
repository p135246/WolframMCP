from __future__ import annotations
import argparse
import logging
import sys
from typing import List, Optional

from mcp.server.fastmcp import FastMCP
from .wolfram import WolframEngine

app = FastMCP("wolfram-mcp")
logger = logging.getLogger("wolfram_mcp.server")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
engine: WolframEngine | None = None

# Resource helpers
_LATEST_NOTEBOOK_PATH: str | None = None

@app.tool()
def evaluate(code: str) -> str:
    """Evaluate a Wolfram Language expression."""
    assert engine is not None, "Engine not initialized"
    return engine.evaluate(code)

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
    engine = WolframEngine(args.kernel_path)
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
