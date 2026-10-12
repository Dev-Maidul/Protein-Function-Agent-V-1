from unittest.mock import patch

from agent.context import AgentContext
from eval.baselines import aggregator_only_from_candidates, diamond_transfer, full_agent, gather_once, llm_no_tools, protrek_transfer
from tools.evidence import Evidence


def test_diamond_transfer_excludes_query_and_homologs():
    ctx = AgentContext(query_sequence="MEEP", query_accession="QUERY1", eval_mode=True)
    ctx.exclude_accessions = {"QUERY1", "HOMOLOG1"}
    ev = Evidence(
        tool="diamond_blast",
        query="q",
        result_summary="ok",
        raw_scores={
            "hits": [
                {"uniprot_accession": "QUERY1", "pident": 100.0},
                {"uniprot_accession": "HOMOLOG1", "pident": 95.0},
                {"uniprot_accession": "OTHER1", "pident": 60.0},
            ]
        },
        source_ids=[],
        source_urls=[],
    )
    hit_ev = Evidence(
        tool="uniprot", query="OTHER1", result_summary="ok",
        raw_scores={"go_terms": [{"go_id": "GO:0001", "term": "x", "evidence": "y"}]},
        source_ids=[], source_urls=[],
    )
    with patch("tools.diamond_blast.run", return_value=ev), patch("tools.uniprot.run", return_value=hit_ev):
        result = diamond_transfer("MEEP", ctx)

    assert result == [{"go_id": "GO:0001", "confidence": 0.6}]


def test_diamond_transfer_no_hits_after_exclusion_returns_empty():
    ctx = AgentContext(query_sequence="MEEP", query_accession="QUERY1", eval_mode=True)
    ctx.exclude_accessions = {"QUERY1"}
    ev = Evidence(
        tool="diamond_blast", query="q", result_summary="ok",
        raw_scores={"hits": [{"uniprot_accession": "QUERY1", "pident": 100.0}]},
        source_ids=[], source_urls=[],
    )
    with patch("tools.diamond_blast.run", return_value=ev):
        result = diamond_transfer("MEEP", ctx)
    assert result == []


def test_protrek_transfer_votes_across_topk():
    ctx = AgentContext(query_sequence="MEEP", query_accession="QUERY1", eval_mode=True)
    protrek_ev = Evidence(
        tool="protrek", query="q", result_summary="ok",
        raw_scores={"ranked": [{"accession": "A1", "description": "d", "score": 1.0}, {"accession": "A2", "description": "d", "score": 0.9}]},
        source_ids=[], source_urls=[],
    )
    a1_ev = Evidence(tool="uniprot", query="A1", result_summary="ok", raw_scores={"go_terms": [{"go_id": "GO:0001"}]}, source_ids=[], source_urls=[])
    a2_ev = Evidence(tool="uniprot", query="A2", result_summary="ok", raw_scores={"go_terms": [{"go_id": "GO:0001"}, {"go_id": "GO:0002"}]}, source_ids=[], source_urls=[])

    with patch("eval.baselines.protrek.run", return_value=protrek_ev), patch(
        "tools.uniprot.run", side_effect=lambda acc: a1_ev if acc == "A1" else a2_ev
    ):
        result = protrek_transfer("MEEP", ctx)

    by_id = {r["go_id"]: r["confidence"] for r in result}
    assert by_id["GO:0001"] == 1.0  # voted by both
    assert by_id["GO:0002"] == 0.5  # voted by only one of two


def test_llm_no_tools_parses_json_from_content():
    class FakeClient:
        def chat(self, messages, tools):
            return {"role": "assistant", "content": 'Sure, here: {"go_terms": [{"go_id": "GO:0005515", "confidence": 0.7}]}'}

    result = llm_no_tools("MEEP", client=FakeClient())
    assert result == [{"go_id": "GO:0005515", "confidence": 0.7}]


def test_llm_no_tools_returns_empty_on_unparseable_content():
    class FakeClient:
        def chat(self, messages, tools):
            return {"role": "assistant", "content": "I cannot determine this without more information."}

    result = llm_no_tools("MEEP", client=FakeClient())
    assert result == []


def test_aggregator_only_from_candidates_strips_to_fmax_shape():
    candidates = [{"go_id": "GO:0001", "confidence": 0.8, "raw_score": 0.8, "evidence_ids": ["E1"], "explanations": ["x"]}]
    assert aggregator_only_from_candidates(candidates) == [{"go_id": "GO:0001", "confidence": 0.8}]


def test_gather_once_and_full_agent_share_one_evidence_pass():
    """Regression test: aggregator_only and full_agent used to each call
    gather_evidence independently, meaning InterProScan/DIAMOND/etc. ran
    twice per protein in eval/run_eval.py. gather_once() + passing its
    result into full_agent(ledger=...) must avoid a second pipeline run."""
    ctx = AgentContext(query_sequence="MEEP", query_accession="Q1", eval_mode=True)
    fake_ledger = [{"id": "E1", "tool": "diamond_blast_search", "args": {}, "evidence": {"error": None, "result_summary": "x", "raw_scores": {"hits": []}, "source_ids": [], "source_urls": []}}]
    fake_candidates = [{"go_id": "GO:0001", "confidence": 0.5, "raw_score": 0.5, "evidence_ids": ["E1"], "explanations": ["x"]}]

    with patch("eval.baselines.gather_evidence", return_value=fake_ledger) as mock_gather, patch(
        "eval.baselines.aggregate", return_value=fake_candidates
    ):
        ledger, candidates = gather_once(ctx)

    mock_gather.assert_called_once()
    assert ledger == fake_ledger
    assert candidates == fake_candidates

    import json as _json

    class FakeTokenUsage:
        input_tokens = 0
        output_tokens = 0
        total = 0

    class FakeClient:
        usage = FakeTokenUsage()
        max_tokens_per_run = 100000

        def chat(self, messages, tools):
            args = {"function_summary": "x", "go_terms": [], "disagreements_or_uncertainty": "None"}
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "submit_final_answer", "arguments": _json.dumps(args)}}]}

        def budget_exceeded(self):
            return False

    with patch("agent.loop.gather_evidence") as mock_gather2:
        terms, report = full_agent("MEEP", "Q1", ctx=ctx, ledger=ledger, aggregator_candidates=candidates, client=FakeClient())
        _ = terms
    mock_gather2.assert_not_called()
    try:
        import shutil
        shutil.rmtree(report["run_dir"], ignore_errors=True)
    except Exception:
        pass
