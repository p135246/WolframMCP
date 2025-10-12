import os
import pytest
from typing import Iterator
from wolfram_mcp.wolfram import WolframEngine


def _discover_explicit_path() -> str | None:
    """Attempt to locate an executable WolframKernel proactively.

    We replicate the engine's private candidate logic minimally here so that we can
    set WOLFRAM_KERNEL_PATH *before* constructing WolframEngine (which otherwise
    caches the absence). This lets CI environments with a standard install run
    tests instead of skipping.
    """
    candidates = [
        # Common macOS bundle
        "/Applications/Wolfram.app/Contents/MacOS/WolframKernel",
        "/Applications/Wolfram Engine.app/Contents/MacOS/WolframKernel",
        # Typical UNIX style locations
        "/usr/local/Wolfram/Mathematica/Kernel/WolframKernel",
        "/usr/local/Wolfram/Engine/Kernel/WolframKernel",
        "/usr/bin/WolframKernel",
        "/opt/Wolfram/WolframKernel",
    ]
    # Allow override extension
    for p in candidates:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return None


@pytest.fixture(scope="session")
def engine() -> Iterator[WolframEngine]:
    # If user didn't set the env var, try to find one now.
    if not os.environ.get("WOLFRAM_KERNEL_PATH"):
        discovered = _discover_explicit_path()
        if discovered:
            os.environ["WOLFRAM_KERNEL_PATH"] = discovered
    eng = WolframEngine()
    # If kernel resolution failed, skip tests gracefully
    if not eng._kernel_path or not (os.path.exists(eng._kernel_path) and os.access(eng._kernel_path, os.X_OK)):
        pytest.skip("Wolfram Kernel not available; skipping integration tests")
    try:
        out = eng.evaluate("1+1")
        if "2" not in out:
            pytest.skip("Kernel sanity check failed: 1+1 != 2")
        yield eng
    finally:
        eng.close()
