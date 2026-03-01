# Wolfram MCP Server

An MCP server that integrates the Wolfram Engine, providing tools for expression evaluation,
notebook creation and editing, image rendering, and code intelligence via LSP.

## Features

- Evaluate Wolfram Language expressions via Wolfram Engine
- Render graphics and plots as images with deterministic URI-based persistence
- Create, edit, and search Wolfram Notebooks (`.nb`) programmatically
- Batch cell operations with flexible JSON input
- Code intelligence via Wolfram LSP: symbol navigation, hover info, go-to-definition, find references, diagnostics
- Documentation scaffolding for symbols and paclets

## Requirements
- Python 3.10+
- Wolfram Engine installed and accessible (or set `WOLFRAM_KERNEL_PATH`)
- `mcp` Python package

## Installation

### Using uv (recommended)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh  # if you don't have uv yet
uv sync --extra dev --extra examples
```

Run tools through `uv run`:

```bash
uv run ruff check .
uv run pytest -q
uv run wolfram-mcp --transport stdio
```

### Traditional virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .[dev,examples]
```

### Installing only example extras

```bash
pip install .[examples]
```

## Running the Server

```bash
# stdio transport (default)
wolfram-mcp --kernel-path /path/to/WolframKernel --transport stdio

# with code intelligence for a project
wolfram-mcp --project-root /path/to/my/wolfram/project --transport stdio

# HTTP (Server-Sent Events) transport
wolfram-mcp --transport sse
```

### CLI Arguments

| Argument | Description |
|----------|-------------|
| `--kernel-path` | Path to Wolfram Kernel executable. Falls back to `WOLFRAM_KERNEL_PATH` env var, then auto-discovery. |
| `--project-root` | Root directory for code intelligence (LSP). Defaults to current working directory. |
| `--transport` | MCP transport: `stdio` (default), `sse`, or `streamable-http` |
| `--quiet` | Reduce logging (equivalent to `--log-level WARNING`) |
| `--log-level` | Explicit log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

You can configure `WOLFRAM_KERNEL_PATH` once in your VS Code MCP client config (`.vscode/mcp.json`) to avoid repeating the argument.

## MCP Tools

### Evaluation

| Tool | Description | Args |
|------|-------------|------|
| `evaluate` | Evaluate a Wolfram Language expression | `code` |
| `ping` | Health check | (none) |

### Image Rendering

| Tool | Description | Args |
|------|-------------|------|
| `render_image` | Render expression as image, persist to disk, return URI | `code`, `fmt?`, `width?`, `height?`, `dpi?`, `auto_register?`, `include_hash_preview?` |
| `attach_saved_image` | Attach an existing local image file as a URI-only image block | `path` |
| `list_image_resources` | List registered image resource URIs | (none) |

### Notebook Operations

| Tool | Description | Args |
|------|-------------|------|
| `create_notebook` | Create a new `.nb` file | `path`, `cells?` |
| `append_cell` | Append a single cell with optional style | `path`, `cell`, `style?` |
| `append_cells` | Append multiple cells with optional styles | `path`, `contents`, `styles?` |
| `append_cells_json` | Append cells from a JSON string (flexible schema) | `path`, `cells_json` |
| `replace_cell` | Replace cell at 1-based index | `path`, `index`, `cell` |
| `list_cells` | List all cells as strings | `path` |
| `get_cell` | Get a single cell by 1-based index | `path`, `index` |
| `search_notebook` | Search cells for a substring | `path`, `query`, `ignore_case?`, `max_results?` |
| `export_notebook` | Export notebook to a format (e.g. `Plaintext`, `JSON`) | `path`, `format?` |
| `frontend_notebook_example` | Create/edit notebook via Wolfram Front End | `path?` |

### Documentation

| Tool | Description | Args |
|------|-------------|------|
| `create_function_doc` | Create documentation notebook skeleton for a symbol | `symbol`, `out_dir?` |
| `create_paclet_doc_skeleton` | Scaffold paclet documentation directory and guide notebook | `paclet_name`, `out_dir?` |

### Code Intelligence (LSP)

These tools use the Wolfram LSP Server for code navigation and analysis. They require a Wolfram project with `.wl`/`.wls` source files. The LSP client starts lazily on first use. Set `--project-root` to point at your Wolfram project directory.

| Tool | Description | Args |
|------|-------------|------|
| `document_symbols` | Get symbols defined in a file (names, kinds, ranges) | `path`, `depth?` |
| `hover_info` | Get documentation/type info at a position | `path`, `line`, `character` |
| `find_definition` | Go to definition of symbol at a position | `path`, `line`, `character` |
| `find_references` | Find all references to symbol across project | `path`, `line`, `character` |
| `get_diagnostics` | Get errors/warnings from the LSP | `path?` |
| `list_project_files` | List Wolfram source files (`.wl`, `.wls`, `.m`, `.nb`) | `directory?` |
| `read_source_file` | Read a source file with optional line range | `path`, `start_line?`, `end_line?` |

Positions (`line`, `character`) are 0-based, following LSP conventions.

## Image Rendering

`render_image` always writes the rasterized image to disk and returns a URI — no base64 payload is included in the response. Filenames are deterministic SHA-256 hashes of `(code, fmt, width, height, dpi)`, so identical renders reuse the same file.

The response is a list `[text_block, image_block]` where the image block contains: `type`, `format`, `width`, `height`, `exprType`, `success`, `error`, and `uri` (file://...).

### Registration

With `auto_register=True` (the default), image metadata is stored in an in-memory registry keyed by a 32-hex digest. A resource URI (`wolfram://image/byhash/<digest>`) is added to the image block, allowing clients to reference images by stable identifiers. Set `include_hash_preview=True` to also include a `digestPrefix` (first 8 hex chars) for quick human reference.

### Environment Variables

| Variable | Effect |
|----------|--------|
| `WOLFRAM_KERNEL_PATH` | Path to Wolfram Kernel executable |
| `WOLFRAM_MCP_IMAGE_DIR` | Directory for persisted images (default `.wolfram_images`) |

### Example

```bash
export WOLFRAM_KERNEL_PATH="/Applications/Wolfram.app/Contents/MacOS/WolframKernel"
uv run python examples/multimodal_agent.py "Plot[Sin[x], {x,0,Pi}]"
```

## MCP Resources

| URI | Description |
|-----|-------------|
| `wolfram://doc/symbol/template` | Symbol documentation skeleton template |
| `wolfram://notebook/latest` | Path of the last notebook touched |
| `wolfram://doc/symbol/{symbol}` | Documentation page skeleton for a given symbol |
| `wolfram://image/byhash/{digest}` | Metadata for a registered rendered image |

## Notebook Handling Strategy

Notebooks are manipulated using Wolfram Language expressions invoked via the Wolfram Engine.
Edits are performed by importing the notebook, transforming `Cell[...]` expressions, and exporting.

## Security Considerations

Evaluating arbitrary code in Wolfram Engine is powerful; consider sandboxing or restricting input in
production deployments.

## Development

```bash
ruff check .
black .
pytest -q
```

## License
MIT
