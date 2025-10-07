# Wolfram MCP Server

This project implements a Model Context Protocol (MCP) server that integrates the Wolfram Engine
for creating and editing Wolfram Notebooks programmatically.

## Features (Planned / Initial)
- Evaluate Wolfram Language expressions via Wolfram Engine
- Create new Wolfram Notebooks (`.nb`) with initial content
- Append new cells to an existing notebook
- Edit (replace) content of specific cells by index
- List cells / export notebook as plain text or JSON structure

## Requirements
- Python 3.10+
- Wolfram Engine installed and accessible via the `wolframscript` command (or set `WOLFRAMSCRIPT_PATH`)
- `mcp` Python package

## Installation
```bash
python -m pip install -e .[dev]
```

## Running the Server
```bash
# stdio transport (default) - suitable for integration with MCP-aware clients
wolfram-mcp --kernel-path /Applications/Wolfram\ Engine/Contents/MacOS/WolframKernel --transport stdio

# or start an HTTP (Server-Sent Events) transport
wolfram-mcp --transport sse
```
If `--kernel-path` is omitted the Wolfram client will attempt to auto-discover a kernel. In `stdio` mode
do not background the process; the client will own the stdio streams.

## MCP Tools Exposed
| Tool | Description | Args |
|------|-------------|------|
| `evaluate` | Evaluate a Wolfram Language expression | `code` (string) |
| `create_notebook` | Create a new notebook file | `path`, `cells` (optional list of cell contents) |
| `append_cell` | Append a new cell to existing notebook | `path`, `cell` |
| `replace_cell` | Replace content of a notebook cell (by index) | `path`, `index`, `cell` |
| `list_cells` | Return JSON array of cell strings | `path` |
| `export_notebook` | Export notebook to a given format (e.g. `Plaintext`, `JSON`) | `path`, `format` |
| `create_function_doc` | Create starter documentation notebook for a symbol | `symbol`, `out_dir?` |
| `create_paclet_doc_skeleton` | Scaffold paclet doc directory & guide notebook | `paclet_name`, `out_dir?` |
| `ping` | Health check | (none) |

## Notebook Handling Strategy
We manipulate notebooks using Wolfram Language expressions invoked in batch mode via `wolframscript`.
Edits are performed by importing the notebook, transforming the `Cell[...]` expressions, and exporting.

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
