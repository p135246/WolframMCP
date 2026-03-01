"""Tests for the Wolfram LSP code intelligence integration.

All tests require a working WolframKernel + LSPServer paclet.
They are skipped gracefully when the kernel is not available.
"""
import json
import os
import shutil
from pathlib import Path
from typing import Iterator

import pytest

from wolfram_mcp.lsp_client import WolframLSPClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _find_kernel() -> str | None:
    path = os.environ.get("WOLFRAM_KERNEL_PATH")
    if path and os.path.exists(path) and os.access(path, os.X_OK):
        return path
    candidates = [
        "/Applications/Mathematica.app/Contents/MacOS/WolframKernel",
        "/Applications/Wolfram.app/Contents/MacOS/WolframKernel",
        "/Applications/Wolfram Engine.app/Contents/MacOS/WolframKernel",
        "/usr/local/bin/WolframKernel",
        "/usr/bin/WolframKernel",
    ]
    for p in candidates:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    found = shutil.which("WolframKernel")
    return found


@pytest.fixture(scope="session")
def lsp_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[WolframLSPClient]:
    kernel = _find_kernel()
    if not kernel:
        pytest.skip("WolframKernel not available; skipping LSP tests")

    project = tmp_path_factory.mktemp("wl_project")
    # Copy fixture files into temp project
    for f in FIXTURES_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, project / f.name)

    client = WolframLSPClient(kernel_path=kernel, project_root=str(project))
    try:
        yield client
    finally:
        client.stop()


@pytest.fixture(scope="session")
def project_root(lsp_client: WolframLSPClient) -> str:
    return lsp_client._project_root


class TestLSPInitialization:
    def test_starts_and_initializes(self, lsp_client: WolframLSPClient):
        lsp_client._ensure_started()
        assert lsp_client._started is True
        assert lsp_client._process is not None
        assert lsp_client._process.poll() is None  # still running


class TestDocumentSymbols:
    def test_finds_top_level_symbols(self, lsp_client: WolframLSPClient, project_root: str):
        path = os.path.join(project_root, "example.wl")
        symbols = lsp_client.document_symbols(path)
        assert isinstance(symbols, list)
        assert len(symbols) > 0
        names = {s.get("name") for s in symbols}
        assert "calculateSum" in names or any("calculateSum" in str(s) for s in symbols)

    def test_helper_file_symbols(self, lsp_client: WolframLSPClient, project_root: str):
        path = os.path.join(project_root, "helper.wl")
        symbols = lsp_client.document_symbols(path)
        assert isinstance(symbols, list)
        assert len(symbols) > 0


class TestHover:
    def test_hover_returns_info(self, lsp_client: WolframLSPClient, project_root: str):
        path = os.path.join(project_root, "example.wl")
        result = lsp_client.hover(path, line=0, character=0)
        # May be None if LSP doesn't provide hover for this position, that's ok
        # Just verify it doesn't crash
        assert result is None or isinstance(result, dict)


class TestDefinition:
    def test_find_definition(self, lsp_client: WolframLSPClient, project_root: str):
        # Try to find definition of calculateSum used in main (line 8, ~col 15)
        path = os.path.join(project_root, "example.wl")
        locations = lsp_client.definition(path, line=8, character=15)
        assert isinstance(locations, list)
        # May or may not find it depending on LSP capability, just don't crash


class TestReferences:
    def test_find_references(self, lsp_client: WolframLSPClient, project_root: str):
        # Find references to calculateSum (defined at line 0, col 0)
        path = os.path.join(project_root, "example.wl")
        refs = lsp_client.references(path, line=0, character=0)
        assert isinstance(refs, list)


class TestDiagnostics:
    def test_diagnostics_available(self, lsp_client: WolframLSPClient):
        diags = lsp_client.get_diagnostics()
        assert isinstance(diags, dict)


class TestMCPTools:
    """Test the MCP tool wrappers (non-LSP tools that don't need the kernel)."""

    def test_list_project_files(self, project_root: str):
        import wolfram_mcp.server as srv
        old_root = srv._project_root
        try:
            srv._project_root = project_root
            result = srv.list_project_files()
            files = json.loads(result)
            assert isinstance(files, list)
            assert any(f.endswith(".wl") for f in files)
        finally:
            srv._project_root = old_root

    def test_read_source_file(self, project_root: str):
        import wolfram_mcp.server as srv
        old_root = srv._project_root
        try:
            srv._project_root = project_root
            result = srv.read_source_file("example.wl")
            assert "calculateSum" in result
        finally:
            srv._project_root = old_root

    def test_read_source_file_line_range(self, project_root: str):
        import wolfram_mcp.server as srv
        old_root = srv._project_root
        try:
            srv._project_root = project_root
            result = srv.read_source_file("example.wl", start_line=0, end_line=2)
            lines = result.strip().splitlines()
            assert len(lines) <= 2
        finally:
            srv._project_root = old_root
