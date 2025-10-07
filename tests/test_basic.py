import os
import pytest
from wolfram_mcp.wolfram import WolframEngine

@pytest.mark.skipif(not os.environ.get("CI"), reason="Requires wolframscript in CI environment")
def test_evaluate_addition():
    eng = WolframEngine()
    out = eng.evaluate("2+2")
    assert "4" in out
