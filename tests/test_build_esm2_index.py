import numpy as np

from tools import build_esm2_index as b


def test_checkpoint_resume_preserves_scattered_writes(tmp_path, monkeypatch):
    """Regression test for a real bug caught during this project's Phase 1
    fixes (see docs/TOOLS.md): embeddings are written at positions scattered
    across the array by length-sorted rank (`order[]` in _embed_all), not at
    contiguous original indices. The old _load_checkpoint restored only
    `embeddings[:done]` -- a contiguous slice -- silently zeroing out most
    already-computed rows on every resume (1,532/5,000 rows corrupted in the
    real run that exposed this). The fix loads the full saved array as-is."""
    monkeypatch.setattr(b, "CHECKPOINT_PATH", tmp_path / "ckpt.npz")

    n, dim = 10, 4
    embeddings = np.zeros((n, dim), dtype=np.float32)
    # Simulate having embedded 6 sorted-rank items whose ORIGINAL indices are
    # scattered, including some >= done (3) -- exactly the case that broke.
    written_positions = [9, 0, 7, 2, 5, 8]  # 6 items processed, done=6
    for i, pos in enumerate(written_positions):
        embeddings[pos] = float(i + 1)
    np.savez(b.CHECKPOINT_PATH, embeddings=embeddings, done=len(written_positions))

    restored, done = b._load_checkpoint(n, dim)

    assert done == 6
    for pos in written_positions:
        assert restored[pos].sum() != 0, f"row {pos} was wiped on restore"
    untouched = set(range(n)) - set(written_positions)
    for pos in untouched:
        assert restored[pos].sum() == 0
