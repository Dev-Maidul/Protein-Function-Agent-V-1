from unittest.mock import patch

from agent.dispatch import AgentContext
from eval.baselines import diamond_transfer, llm_no_tools, protrek_transfer
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
