"""The web app loads, runs the example change and renders every page without errors."""
from streamlit.testing.v1 import AppTest


def test_app_runs_example_offline(monkeypatch):
    monkeypatch.setenv("CHANGEGUARD_OFFLINE", "1")
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    assert not at.exception
    next(b for b in at.button if b.label == "Analyze change").click().run()
    assert not at.exception
    assert any(m.label == "Tests passed" for m in at.metric)
