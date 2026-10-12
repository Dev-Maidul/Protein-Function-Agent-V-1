"""InterPro2GO mapping lookup. The InterProScan API's own JSON response already
embeds a `goXRefs` list per entry for *most* entries (tools/interproscan.py
already uses this), but not every InterPro entry has that field populated in
every response, and relying only on what one API call happens to return is a
single point of failure for the agent's GO-grounding evidence. This module
gives a second, independent path to the same mapping: EBI's canonical
interpro2go flat file (same curators, same mapping, downloaded once and
cached), so an InterPro ID can be cross-referenced even if the live
InterProScan response omitted its GO cross-references.

Source: https://ftp.ebi.ac.uk/pub/databases/interpro/current_release/interpro2go
(EBI, free for all use, same license class as the other EBI tools in this
project -- see docs/TOOLS.md). Verified live: 3,064,685 bytes, 29,996 lines,
format `InterPro:IPRxxxxxx <name> > GO:<name> ; GO:<id>` (one line per
InterPro-GO pair; an entry can have multiple lines).
"""
from __future__ import annotations

import functools
import re
from pathlib import Path

import requests

MAPPING_URL = "https://ftp.ebi.ac.uk/pub/databases/interpro/current_release/interpro2go"
MAPPING_PATH = Path(__file__).parent.parent / "data" / "interpro2go.txt"
_LINE_RE = re.compile(r"^InterPro:(IPR\d+)\s.*;\s*(GO:\d+)\s*$")


def download_mapping(path: Path | None = None) -> Path:
    path = path or MAPPING_PATH
    resp = requests.get(MAPPING_URL, timeout=120)
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text)
    return path


@functools.lru_cache(maxsize=1)
def _load_mapping(path: str) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for line in Path(path).read_text().splitlines():
        if line.startswith("!") or not line.strip():
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        ipr_id, go_id = m.group(1), m.group(2)
        mapping.setdefault(ipr_id, []).append(go_id)
    return mapping


def get_go_terms(interpro_id: str, mapping_path: Path | None = None) -> list[str]:
    """Returns the GO IDs mapped to an InterPro entry (empty list if none or
    if the entry isn't in the mapping file). Lazily downloads the mapping
    file on first use if it isn't already cached locally."""
    mapping_path = mapping_path or MAPPING_PATH
    if not mapping_path.exists():
        download_mapping(mapping_path)
    return _load_mapping(str(mapping_path)).get(interpro_id, [])
