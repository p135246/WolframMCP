def test_evaluate_addition(engine):
    out = engine.evaluate("2+2")
    assert "4" in out
