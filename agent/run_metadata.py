"""Captures environment provenance for a run's saved metadata -- added after
the 2026-10-29 ProTrek environment bug (full_agent/aggregator_only silently
losing ProTrek evidence because the interpreter running them didn't have the
same package versions as the one used to verify ProTrek standalone). Recording
this per run means a future "the numbers changed" question can be answered by
diffing two runs' metadata.json instead of re-deriving it from memory.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


def _version(module_name: str) -> str | None:
    try:
        mod = __import__(module_name)
    except ImportError:
        return None
    return getattr(mod, "__version__", "unknown")


def capture_run_metadata() -> dict:
    from tools import protrek

    protrek_pool_size = None
    if protrek.DEFAULT_CACHE_PATH.exists():
        import numpy as np

        protrek_pool_size = int(len(np.load(protrek.DEFAULT_CACHE_PATH)["accessions"]))

    return {
        "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "package_versions": {
            "torch": _version("torch"),
            "transformers": _version("transformers"),
            "torchmetrics": _version("torchmetrics"),
            "faiss": _version("faiss"),
        },
        "protrek_candidate_pool_size": protrek_pool_size,
    }
