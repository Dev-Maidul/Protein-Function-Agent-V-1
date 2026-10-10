"""Shared test helpers: tests run entirely offline against saved fixtures
(tests/fixtures/, each captured from one real live call -- see docs/TOOLS.md)."""
import json
from pathlib import Path
from unittest.mock import MagicMock

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture_json(name: str):
    return json.loads((FIXTURES_DIR / name).read_text())


def fake_response(json_body=None, text_body="", status_code=200, headers=None):
    """A requests.Response-shaped MagicMock, built from a saved fixture."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.text = text_body or (json.dumps(json_body) if json_body is not None else "")
    resp.json.return_value = json_body
    resp.ok = status_code < 400
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.side_effect = None
    return resp
