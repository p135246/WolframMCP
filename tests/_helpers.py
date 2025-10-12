import os
from wolfram_mcp.wolfram import WolframEngine

_DEF_EXPR = "1+1"


def engine_available() -> bool:
    """Return True if Wolfram Engine appears available.

    Allows override via env:
      WOLFRAM_FORCE_AVAILABLE=1  -> force True
      WOLFRAM_FORCE_UNAVAILABLE=1 -> force False
    """
    if os.environ.get("WOLFRAM_FORCE_AVAILABLE"):
        return True
    if os.environ.get("WOLFRAM_FORCE_UNAVAILABLE"):
        return False
    try:
        kernel_path = os.environ.get("WOLFRAM_KERNEL_PATH")
        eng = WolframEngine(kernel_path)
        out = eng.evaluate(_DEF_EXPR)
        return "2" in out
    except Exception:
        return False
