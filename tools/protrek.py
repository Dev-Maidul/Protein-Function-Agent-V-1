"""ProTrek_35M (westlake-repl, MIT license) -- trimodal (sequence + structure +
natural-language text) contrastive model. Given a protein sequence and a list
of candidate function descriptions, ranks the descriptions by how well they
match the protein -- the agent's main "does this candidate function fit?" tool.

Repo: https://github.com/westlake-repl/ProTrek (vendored, code only, in
vendor/ProTrek/ -- no large files, see docs/TOOLS.md)
Weights: https://huggingface.co/westlake-repl/ProTrek_35M_UniRef50 (~678MB,
reused from this user's earlier Westlake-ProteinAI-Prep project instead of
re-downloading -- see weights/README in that project)

All heavy imports (torch, transformers, torchmetrics, pytorch-lightning, and
the vendored model code) happen lazily inside functions, not at module import
time, so this module stays importable for testing `_score()` without any of
those installed.
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path

from tools.evidence import Evidence, timed_call

PROJECT_ROOT = Path(__file__).parent.parent
VENDOR_DIR = PROJECT_ROOT / "vendor" / "ProTrek"
DEFAULT_WEIGHTS_DIR = PROJECT_ROOT / "weights" / "ProTrek_35M_UniRef50"

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
    import torch
    from model.ProTrek.protrek_trimodal_model import ProTrekTrimodalModel

    config = {
        "protein_config": f"{weights_dir}/esm2_t12_35M_UR50D",
        "text_config": f"{weights_dir}/BiomedNLP-PubMedBERT-base-uncased-abstract-fulltext",
        "structure_config": f"{weights_dir}/foldseek_t12_35M",
        "from_checkpoint": f"{weights_dir}/ProTrek_35M.pt",
    }
    return ProTrekTrimodalModel(**config).eval().to(device)


def _score(model, sequence: str, candidate_texts: list[str]) -> list[tuple[str, float]]:
    import torch

    with torch.no_grad():
        seq_emb = model.get_protein_repr([sequence])
        text_emb = model.get_text_repr(candidate_texts)
        scores = (seq_emb @ text_emb.T / model.temperature).squeeze(0)
    return sorted(zip(candidate_texts, scores.tolist()), key=lambda x: -x[1])


def _run_pipeline(sequence: str, candidate_texts: list[str], weights_dir: Path, device: str | None) -> dict:
    import torch

    device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
    model = _load_model(str(weights_dir), device)
    ranked = _score(model, sequence, candidate_texts)

    top_text, top_score = ranked[0]
    return {
        "result_summary": f"best match ({top_score:+.3f}): \"{top_text}\"",
        "raw_scores": {"ranked": [{"text": t, "score": s} for t, s in ranked], "device": device},
        "source_ids": [],
        "source_urls": ["https://huggingface.co/westlake-repl/ProTrek_35M_UniRef50"],
    }


def run(
    sequence: str,
    candidate_texts: list[str],
    weights_dir: Path | None = None,
    device: str | None = None,
) -> Evidence:
    weights_dir = weights_dir or DEFAULT_WEIGHTS_DIR
    return timed_call("protrek", sequence[:30] + "...", _run_pipeline, sequence, candidate_texts, weights_dir, device)
