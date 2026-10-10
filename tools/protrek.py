"""ProTrek_35M (westlake-repl, MIT license) -- trimodal (sequence + structure +
natural-language text) contrastive model. Given a protein sequence, retrieves
the best-matching function descriptions from a pre-computed pool of ~5,000
real Swiss-Prot "Function [CC]" annotations (data/shared_subset.tsv, built by
tools/build_shared_subset.py; text embeddings cached by
tools/build_protrek_cache.py into data/protrek_text_embeddings.npz) -- this
replaces an earlier version of this tool that only ranked a hand-picked list
of 5 candidate texts, which was too small to be a meaningful test (see
docs/TOOLS.md, Phase 1 fix #1).

In evaluation mode, the caller passes `exclude_accessions` (the query's own
accession plus any close homologs, e.g. from tools/diamond_blast.py at a
chosen identity threshold) so the agent cannot retrieve the query's own
annotation or an near-identical one as if it were independent evidence.

Repo: https://github.com/westlake-repl/ProTrek (vendored, code only, in
vendor/ProTrek/ -- no large files, see docs/TOOLS.md)
Weights: https://huggingface.co/westlake-repl/ProTrek_35M_UniRef50 (~678MB,
reused from this user's earlier Westlake-ProteinAI-Prep project instead of
re-downloading -- see weights/README in that project)

All heavy imports (torch, transformers, torchmetrics, pytorch-lightning, and
the vendored model code) happen lazily inside functions, not at module import
time, so this module stays importable for testing retrieval logic without any
of those installed.
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path

import numpy as np

from tools.evidence import Evidence, timed_call

PROJECT_ROOT = Path(__file__).parent.parent
VENDOR_DIR = PROJECT_ROOT / "vendor" / "ProTrek"
DEFAULT_WEIGHTS_DIR = PROJECT_ROOT / "weights" / "ProTrek_35M_UniRef50"
DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "protrek_text_embeddings.npz"

_MONKEYPATCHES_APPLIED = False


def _apply_monkeypatches() -> None:
    """Three version-mismatch fixes, carried over from this user's earlier,
    already-verified ProTrek demo (Westlake-ProteinAI-Prep/ProTrek/demo_protrek_search.py) --
    the repo pins library APIs from ~2024 that newer installs changed."""
    global _MONKEYPATCHES_APPLIED
    if _MONKEYPATCHES_APPLIED:
        return

    import torchmetrics

    torchmetrics.Accuracy = functools.partial(torchmetrics.Accuracy, task="multiclass", num_classes=2)

    sys.path.insert(0, str(VENDOR_DIR))
    import utils.lr_scheduler as lr_scheduler_module

    def _patched_constant_init(self, optimizer, last_epoch=-1, verbose=False, init_lr=0.0):
        self.init_lr = init_lr
        lr_scheduler_module._LRScheduler.__init__(self, optimizer, last_epoch)

    lr_scheduler_module.ConstantLRScheduler.__init__ = _patched_constant_init

    from transformers.tokenization_utils_base import PreTrainedTokenizerBase

    if not hasattr(PreTrainedTokenizerBase, "batch_encode_plus"):
        def _batch_encode_plus(self, texts, **kwargs):
            return self(texts, **kwargs)

        PreTrainedTokenizerBase.batch_encode_plus = _batch_encode_plus

    _MONKEYPATCHES_APPLIED = True


@functools.lru_cache(maxsize=2)
def _load_model(weights_dir: str, device: str):
    _apply_monkeypatches()
    from model.ProTrek.protrek_trimodal_model import ProTrekTrimodalModel

    config = {
        "protein_config": f"{weights_dir}/esm2_t12_35M_UR50D",
        "text_config": f"{weights_dir}/BiomedNLP-PubMedBERT-base-uncased-abstract-fulltext",
        "structure_config": f"{weights_dir}/foldseek_t12_35M",
        "from_checkpoint": f"{weights_dir}/ProTrek_35M.pt",
    }
    return ProTrekTrimodalModel(**config).eval().to(device)


@functools.lru_cache(maxsize=1)
def _load_text_cache(cache_path: str) -> dict:
    data = np.load(cache_path)
    return {
        "embeddings": data["embeddings"],
        "accessions": data["accessions"],
        "descriptions": data["descriptions"],
    }


def _embed_sequence(model, sequence: str) -> np.ndarray:
    import torch

    with torch.no_grad():
        emb = model.get_protein_repr([sequence])
    return emb.cpu().numpy()[0]


def _temperature(model) -> float:
    import torch

    t = model.temperature
    return t.detach().item() if isinstance(t, torch.Tensor) else float(t)


def _retrieve(
    query_emb: np.ndarray,
    temperature: float,
    cache: dict,
    top_k: int,
    exclude_accessions: set[str] | None,
) -> tuple[list[dict], int]:
    accessions = cache["accessions"]
    mask = np.ones(len(accessions), dtype=bool)
    if exclude_accessions:
        mask = ~np.isin(accessions, list(exclude_accessions))

    pool_embeddings = cache["embeddings"][mask]
    pool_accessions = accessions[mask]
    pool_descriptions = cache["descriptions"][mask]

    scores = (pool_embeddings @ query_emb) / temperature
    top_idx = np.argsort(-scores)[:top_k]

    ranked = [
        {
            "accession": str(pool_accessions[i]),
            "description": str(pool_descriptions[i]),
            "score": float(scores[i]),
        }
        for i in top_idx
    ]
    excluded_count = int((~mask).sum())
    return ranked, excluded_count


def _run_pipeline(
    sequence: str,
    top_k: int,
    exclude_accessions: set[str] | None,
    weights_dir: Path,
    cache_path: Path,
    device: str | None,
) -> dict:
    import torch

    device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
    model = _load_model(str(weights_dir), device)
    cache = _load_text_cache(str(cache_path))

    query_emb = _embed_sequence(model, sequence)
    ranked, excluded_count = _retrieve(query_emb, _temperature(model), cache, top_k, exclude_accessions)

    top = ranked[0] if ranked else None
    result_summary = (
        f"top match ({top['score']:+.3f}, {top['accession']}): \"{top['description'][:100]}\""
        if top
        else "no candidates retrieved"
    )
    return {
        "result_summary": result_summary,
        "raw_scores": {
            "ranked": ranked,
            "candidate_pool_size": len(cache["accessions"]) - excluded_count,
            "excluded_count": excluded_count,
            "device": device,
        },
        "source_ids": [r["accession"] for r in ranked],
        "source_urls": [f"https://www.uniprot.org/uniprotkb/{r['accession']}" for r in ranked],
    }


def run(
    sequence: str,
    top_k: int = 10,
    exclude_accessions: set[str] | None = None,
    weights_dir: Path | None = None,
    cache_path: Path | None = None,
    device: str | None = None,
) -> Evidence:
    weights_dir = weights_dir or DEFAULT_WEIGHTS_DIR
    cache_path = cache_path or DEFAULT_CACHE_PATH
    return timed_call(
        "protrek",
        sequence[:30] + "...",
        _run_pipeline,
        sequence,
        top_k,
        exclude_accessions,
        weights_dir,
        cache_path,
        device,
    )
