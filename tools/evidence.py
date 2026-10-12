"""Standard return type every tool wrapper in tools/ must produce.

A single, uniform shape lets the agent (Phase 2) treat every tool identically:
log it, cite it by ID in the final report, and never crash on a tool failure
(errors are data, not exceptions).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import requests


@dataclass
class Evidence:
    tool: str
    query: str
    result_summary: str
    raw_scores: dict[str, Any] = field(default_factory=dict)
    source_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    runtime_s: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


def timed_call(tool: str, query: str, fn, *args, **kwargs) -> Evidence:
    """Run fn(*args, **kwargs) -> Evidence-shaped dict or Evidence, timing it
    and converting any exception into an error-carrying Evidence instead of
    letting it propagate (a tool failing must never crash the agent loop)."""
    start = time.monotonic()
    try:
        result = fn(*args, **kwargs)
        if isinstance(result, Evidence):
            result.runtime_s = round(time.monotonic() - start, 3)
            return result
        result["runtime_s"] = round(time.monotonic() - start, 3)
        return Evidence(tool=tool, query=query, **result)
    except Exception as exc:  # noqa: BLE001 - deliberate: tool errors become data
        return Evidence(
            tool=tool,
            query=query,
            result_summary="tool call failed",
            runtime_s=round(time.monotonic() - start, 3),
            error=f"{type(exc).__name__}: {exc}",
        )


def get_with_retry(url: str, max_retries: int = 3, base_delay: float = 2.0, **kwargs) -> requests.Response:
    """GET with retry + exponential backoff on rate-limiting/transient server
    errors (429/502/503/504) -- found necessary during the Phase 3 full test-set
    run, where thousands of sequential EBI calls (QuickGO/UniProt) triggered
    rate-limiting that silently turned into empty results everywhere, not a
    single clear error. A real 4xx/other failure still raises immediately."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, **kwargs)
            if resp.status_code in (429, 502, 503, 504) and attempt < max_retries - 1:
                time.sleep(base_delay * (2**attempt))
                continue
            return resp
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2**attempt))
    raise last_exc


def save_fixture(name: str, payload: Any, fixtures_dir: str | Path = "tests/fixtures") -> Path:
    """Save a raw tool response for offline pytest use. Called manually,
    once per tool, against a real live call -- never auto-generated."""
    path = Path(fixtures_dir)
    path.mkdir(parents=True, exist_ok=True)
    out = path / name
    out.write_text(json.dumps(payload, indent=2, default=str))
    return out


def load_fixture(name: str, fixtures_dir: str | Path = "tests/fixtures") -> Any:
    path = Path(fixtures_dir) / name
    return json.loads(path.read_text())
