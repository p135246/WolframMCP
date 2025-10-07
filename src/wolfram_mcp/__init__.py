"""Wolfram MCP package root.

Avoid importing the server module at package import time so that
`python -m wolfram_mcp.server` does not trigger the runpy RuntimeWarning
about a module already existing in sys.modules prior to execution.
"""

__all__: list[str] = []

__version__ = "0.1.0"
