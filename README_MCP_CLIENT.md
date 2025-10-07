# Using the Wolfram MCP Server from a Client

Below is an example JSON-RPC interaction once the server is running.

## List Tools
```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
```

## Evaluate
```json
{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "evaluate", "arguments": {"code": "Integrate[Sin[x], x]"}}}
```

## Create Notebook
```json
{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "create_notebook", "arguments": {"path": "example.nb", "cells": ["2+2", "Plot[Sin[x], {x,0,6.28}]"]}}}
```

## Append Cell
```json
{"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "append_cell", "arguments": {"path": "example.nb", "cell": "Expand[(x+y)^3]"}}}
```

## Replace Cell
```json
{"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "replace_cell", "arguments": {"path": "example.nb", "index": 1, "cell": "Factor[x^3 + 3 x^2 y + 3 x y^2 + y^3]"}}}
```

## List Cells
```json
{"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "list_cells", "arguments": {"path": "example.nb"}}}
```

## Export Notebook
```json
{"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "export_notebook", "arguments": {"path": "example.nb", "format": "Plaintext"}}}
```

## Frontend Notebook Example (creates & edits via NotebookWrite)
```json
{"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "frontend_notebook_example", "arguments": {"path": "frontend_demo.nb"}}}
```

Adjust to your client library conventions as needed.
