import json
import base64  # still used for validating PNG header in first test where data originally present (may be removed later)



def test_evaluate_raster_basic(engine):
    result_json = engine.evaluate_raster("Plot[Sin[x], {x,0,Pi}]", fmt="PNG", width=200, height=150)
    print("Result JSON:", result_json)
    data = json.loads(result_json)
    assert data.get("format") == "PNG"
    assert data.get("success") is True
    assert isinstance(data["width"], (int, float)) and data["width"] > 0
    assert isinstance(data["height"], (int, float)) and data["height"] > 0
    assert data["data"] is not None and isinstance(data["data"], str)
    img_bytes = base64.b64decode(data["data"])
    assert len(img_bytes) > 0


def test_render_image_basic(engine, tmp_path):
    import wolfram_mcp.server as server_module
    server_module.engine = engine  # type: ignore[attr-defined]
    from wolfram_mcp.server import render_image
    import os, pathlib

    # Render now always produces a URI (no base64 data returned)
    # Use temp dir override for cleanliness
    env_backup = os.environ.get("WOLFRAM_MCP_IMAGE_DIR")
    try:
        os.environ["WOLFRAM_MCP_IMAGE_DIR"] = str(tmp_path)
        result = render_image("Plot[Cos[x], {x,0,Pi}]", fmt="PNG", width=220, height=140)
        assert isinstance(result, list)
        image_block = next(b for b in result if isinstance(b, dict) and b.get("type") == "image")
        assert image_block.get("format") == "PNG"
        assert image_block.get("width") > 0 and image_block.get("height") > 0
        uri = image_block.get("uri")
        assert uri and uri.startswith("file://")
        # Verify file exists
        from urllib.parse import urlparse, unquote
        parsed = urlparse(uri)
        file_path = pathlib.Path(unquote(parsed.path))
        assert file_path.exists()
        assert file_path.read_bytes().startswith(b"\x89PNG")
        # Ensure no base64 data key
        assert "data" not in image_block
    finally:
        if env_backup is not None:
            os.environ["WOLFRAM_MCP_IMAGE_DIR"] = env_backup
        else:
            os.environ.pop("WOLFRAM_MCP_IMAGE_DIR", None)


def test_render_image_persists(engine, tmp_path):
    import wolfram_mcp.server as server_module
    server_module.engine = engine  # type: ignore[attr-defined]
    from wolfram_mcp.server import render_image
    import pathlib, os

    env_backup = os.environ.get("WOLFRAM_MCP_IMAGE_DIR")
    try:
        os.environ["WOLFRAM_MCP_IMAGE_DIR"] = str(tmp_path)
        result = render_image("Plot[Sin[x], {x,0,Pi}]", fmt="PNG", width=180, height=120)
        image_block = next(b for b in result if isinstance(b, dict) and b.get("type") == "image")
        uri = image_block.get("uri")
        assert uri and uri.startswith("file://")
        from urllib.parse import urlparse, unquote
        parsed = urlparse(uri)
        file_path = pathlib.Path(unquote(parsed.path))
        assert file_path.exists()
        assert file_path.stat().st_size > 0
    finally:
        if env_backup is not None:
            os.environ["WOLFRAM_MCP_IMAGE_DIR"] = env_backup
        else:
            os.environ.pop("WOLFRAM_MCP_IMAGE_DIR", None)