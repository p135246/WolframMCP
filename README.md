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

### Using uv (recommended for speed & pinning)

If you use the fast `uv` package manager you can skip creating a venv manually:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh  # if you don't have uv yet
uv sync --extra dev --extra examples  # installs into an .venv managed by uv
```

Run tools through `uv run` (it automatically picks the environment):

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

Deactivate anytime with `deactivate`.

### Installing only example extras

If you already installed the base package but now want the example dependencies (Pillow & anyio):
```bash
pip install .[examples]
```

## Running the Server
```bash
# stdio transport (default) - suitable for integration with MCP-aware clients
wolfram-mcp --kernel-path /Applications/Wolfram\ Engine/Contents/MacOS/WolframKernel --transport stdio

# or start an HTTP (Server-Sent Events) transport
wolfram-mcp --transport sse
```
If `--kernel-path` is omitted the server will look for the `WOLFRAM_KERNEL_PATH` environment variable, then fall back to auto-discovery. In `stdio` mode do not background the process; the client will own the stdio streams.

You can configure the VS Code MCP client (see `.vscode/mcp.json`) to set `WOLFRAM_KERNEL_PATH` once, avoiding repeating the argument.

Example:
```bash
export WOLFRAM_KERNEL_PATH="/Applications/Wolfram.app/Contents/MacOS/WolframKernel"
wolfram-mcp --transport stdio
```

## MCP Tools Exposed
| Tool | Description | Args |
|------|-------------|------|
| `evaluate` | Evaluate a Wolfram Language expression | `code` (string) |
| `render_image` | Evaluate expression and return a multimodal image (now supports `mode` = data|uri|both) | `code`, `fmt?`, `width?`, `height?`, `dpi?`, `mode?` |
| `create_notebook` | Create a new notebook file | `path`, `cells` (optional list of cell contents) |
| `append_cell` | Append a new cell to existing notebook | `path`, `cell` |
| `replace_cell` | Replace content of a notebook cell (by index) | `path`, `index`, `cell` |
| `list_cells` | Return JSON array of cell strings | `path` |
| `export_notebook` | Export notebook to a given format (e.g. `Plaintext`, `JSON`) | `path`, `format` |
| `create_function_doc` | Create starter documentation notebook for a symbol | `symbol`, `out_dir?` |
| `create_paclet_doc_skeleton` | Scaffold paclet doc directory & guide notebook | `paclet_name`, `out_dir?` |
| `ping` | Health check | (none) |

### Multimodal Usage

Use the `render_image` tool when integrating with multimodal LLMs that accept image inputs.

#### Output Modes

To reduce token bloat from large base64 payloads you can choose how image data is returned:

| Mode | What you get | When to use |
|------|--------------|-------------|
| `data` (default) | Base64 inline image (blocks: text + `{type:image,data,...}`) | Simplicity, no filesystem access required by client |
| `uri` | Image written to disk; block contains `uri` (file://...) + metadata (no base64) | Large images, repeated reuse, agent wants to cache / stream lazily |
| `both` | Both base64 and uri | Transitional / debugging |

You can set the mode per call (`mode="uri"`) or globally via `WOLFRAM_MCP_IMAGE_MODE` env var.

Images are stored (for `uri` / `both`) in a directory specified by `WOLFRAM_MCP_IMAGE_DIR` (default: `.wolfram_images/`). Filenames are deterministic SHA256 hashes of `(code, fmt, width, height, dpi)` so repeated identical renders reuse the same file without duplication.

Example block list (default `data` mode):

```jsonc
{
	"text": "Rendered Plot[Sin[x], {x,0,Pi}] => PNG 300x200",
	"image": {"type": "image", "format": "PNG", "data": "<base64>", "width":300, "height":200},
	"success": true,
	"exprType": "Graphics"
}
```

An example client is provided in `examples/multimodal_agent.py` that:

1. Launches the server (stdio) with a real `WOLFRAM_KERNEL_PATH`.
2. Invokes `render_image`.
3. Produces an ASCII preview (optional, Pillow) and shows how you'd forward to an LLM.

Run it (ensure Pillow installed if you want ASCII preview):
```bash
python -m pip install pillow
export WOLFRAM_KERNEL_PATH="/Applications/Wolfram.app/Contents/MacOS/WolframKernel"
python examples/multimodal_agent.py "Plot[Sin[x], {x,0,Pi}]"
```

Example in `uri` mode (list form):

```jsonc
[
	{"type":"text","text":"Rendered Plot[Sin[x], {x,0,Pi}] => PNG 300x200"},
	{"type":"image","format":"PNG","uri":"file:///abs/path/.wolfram_images/img_ab12cd34_300x200.png","width":300,"height":200}
]
```

Environment variables summary:

| Variable | Effect |
|----------|--------|
| `WOLFRAM_MCP_IMAGE_MODE` | Default `mode` (data|uri|both) if parameter omitted |
| `WOLFRAM_MCP_IMAGE_DIR` | Directory for persisted images (default `.wolfram_images`) |

With uv:
```bash
export WOLFRAM_KERNEL_PATH="/Applications/Wolfram.app/Contents/MacOS/WolframKernel"
uv run python examples/multimodal_agent.py "Plot[Sin[x], {x,0,Pi}]"
```

## Image Persistence & Context Attachment Workflow

Agents (or users) that want to both display an image immediately and persist it for future context / referencing should follow this workflow:

1. Initial render (preview): Call `render_image` with `mode="data"` (or omit `mode`) to get a quick inline base64 image. Validate output / tweak code.
2. Persist deterministically: Re‑invoke `render_image` with identical `code` (and desired `width`/`height`/`dpi`) but `mode="uri"` (or `mode="both"` if you also still need the base64). The server writes a hashed filename into `.wolfram_images/` and returns a `uri`.
3. Attach to context: Reference the relative path (e.g. `.wolfram_images/img_<hash>_1200x900.png`) inside project markdown or tool output. Because the filename hash is deterministic, repeated identical renders will not duplicate files.
4. Reuse cheaply: For subsequent discussions, request `mode="uri"` only to avoid large base64 payloads while still having a stable on-disk artifact clients can open or embed.
5. Housekeeping (optional): Periodically scan `.wolfram_images/` for unreferenced files if storage cleanup is desired.

### Minimal Example

```python
code = "Plot[Sin[x], {x,0,Pi}]"

# 1. Quick preview
preview = client.call_tool("wolfram/render_image", {"code": code, "width": 400, "height": 300})

# 2. Persist (uri only)
persisted = client.call_tool("wolfram/render_image", {"code": code, "width": 400, "height": 300, "mode": "uri"})
image_uri = next(b["uri"] for b in persisted if b.get("type") == "image")

# 3. Use image_uri in markdown / further tool calls
print("Persisted at:", image_uri)
```

See `examples/parametric_plot.md` for a concrete persisted example that follows this pattern.

## Agent Auto-Workflow Guidance

If the user asks any Wolfram Language question likely to produce a visual (e.g. mentions `Plot`, `Show`, `Graphics`, `Graphics3D`, `ListPlot`, `DensityPlot`, `ContourPlot`, `ParametricPlot`, `Geo*`, or contains `Plot3D[` / `ParametricPlot3D[`), an autonomous MCP client can proactively:

1. Detect intent: Parse the user utterance for WL graphics function names.
2. Decide render size: Default to a mid-size (e.g. width=600, height=450) unless the user specifies `ImageSize` or explicit dimensions. Respect provided `ImageSize -> n` by mapping to width ~ n, height ~ n * 3/4 unless square geometry implied.
3. First attempt (preview): Call `render_image` with `mode="data"` (or global default). If the base64 payload is very large (e.g. > ~1MB) immediately plan a second call with `mode="uri"` to persist and then discard base64.
4. Persistence upgrade: Re-call `render_image` using identical parameters but `mode="uri"` (or `both` if the client still needs inline viewing) to obtain a stable file URI.
5. Context attachment: Insert a short markdown snippet referencing the relative path (e.g. `.wolfram_images/<file>.png`) into the conversation or documentation so downstream reasoning steps can cite the image without retransmitting base64.
6. Deduplication check: Before re-rendering, optionally hash the candidate code locally or just invoke `render_image mode=uri`; because hashing is deterministic, no duplicate file will be created if it already exists.
7. Parameter refinement loop: If the user later edits parameters (e.g. adds `PlotRange`, changes colors, increases `PlotPoints`), treat that as a new code string → new hash → new persisted file. Provide a diff summary (changed options) and link both filenames.
8. Minimal mode policy: After successful persistence, default future renders to `mode="uri"` unless the user explicitly asks to “show inline base64” or requests an encoded form.
9. Cleanup strategy (optional): Periodically scan `.wolfram_images/` to find files not referenced in any markdown or recent conversation turns if storage pressure arises.

### Pseudocode Decision Logic

```python
def maybe_render(user_text: str):
	graphics_keywords = [
		'Plot', 'Plot3D', 'ParametricPlot', 'ParametricPlot3D', 'DensityPlot',
		'ContourPlot', 'ListPlot', 'ListPointPlot3D', 'Graphics', 'Graphics3D',
		'Show', 'Geo', 'GeoListPlot'
	]
	if any(k + '[' in user_text for k in graphics_keywords):
		code = extract_wl_code(user_text)  # client-specific parsing
		size = choose_size(user_text)      # heuristic from ImageSize or default
		preview = call_tool('wolfram/render_image', {
			'code': code,
			'width': size.width,
			'height': size.height,
			'mode': 'data'
		})
		if image_too_large(preview):
			persisted = call_tool('wolfram/render_image', {
				'code': code,
				'width': size.width,
				'height': size.height,
				'mode': 'uri'
			})
			return build_markdown_reference(persisted)
		return preview
```

### Heuristic Defaults

| Situation | Width × Height | Rationale |
|-----------|----------------|-----------|
| Unspecific 2D plot | 600 × 400 | Readable, moderate payload |
| 3D plot / parametric | 600 × 450 | Slightly taller for perspective |
| User says “high resolution” | 900 × 675 (or honor ImageSize) | Higher detail while still < ~1MB PNG for many surfaces |
| Thumbnail / preview | 300 × 225 | Fast iteration |

### Error Handling Guidance

| Failure | Agent Action |
|---------|--------------|
| Kernel not initialized | Surface instructions for starting server with `--kernel-path` or env var; retry once after short backoff |
| Invalid mode value | Fall back to `data`; inform user mode must be data|uri|both |
| File write failure (permission) | Return inline base64 (data) and warn user to adjust permissions or `WOLFRAM_MCP_IMAGE_DIR` |
| Oversized base64 (>2MB) | Auto-repeat call in `mode="uri"` and replace inline payload with markdown link |

### Why Deterministic Filenames Matter
Deterministic hashing allows the agent to treat the image path as an idempotent cache key—no need to build a separate caching layer. It also makes referencing stable across sessions if the working directory persists.

### One-Line Agent Policy Template
“When user input contains a recognized WL graphics function, automatically render with `mode=data`, then persist with `mode=uri` and reference the relative image path to avoid repeated base64 transmission.”


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
