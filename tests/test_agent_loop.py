import json
import shutil
from unittest.mock import patch

from agent.loop import AgentRun, _parse_go_terms_from_text
from tools.evidence import Evidence


class FakeTokenUsage:
    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0

    @property
    def total(self):
        return self.input_tokens + self.output_tokens


class FakeLLMClient:
    """Scripted replacement for agent.llm_client.LLMClient -- one scripted
    message per .chat() call, so the review loop's control flow (retry-once,
    then recover-from-text) can be tested without a live LLM server."""

    def __init__(self, scripted_messages, max_tokens_per_run=100000, budget_exceeded_from=None):
        self._scripted = list(scripted_messages)
        self._calls = 0
        self.usage = FakeTokenUsage()
        self.max_tokens_per_run = max_tokens_per_run
        self._budget_exceeded_from = budget_exceeded_from

    def chat(self, messages, tools):
        msg = self._scripted[min(self._calls, len(self._scripted) - 1)]
        self._calls += 1
        self.usage.input_tokens += 100
        self.usage.output_tokens += 50
        return msg

    def budget_exceeded(self):
        if self._budget_exceeded_from is None:
            return False
        return self._calls >= self._budget_exceeded_from


def _final_answer_msg(args=None, call_id="call_1"):
    args = args or {
        "function_summary": "A test protein (E1).",
        "go_terms": [{"go_id": "GO:0006915", "confidence": 0.8, "evidence_ids": ["E1"]}],
        "disagreements_or_uncertainty": "None",
    }
    return {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "submit_final_answer", "arguments": json.dumps(args)}}]}


def _text_only_msg(text):
    return {"role": "assistant", "content": text, "tool_calls": []}


FAKE_LEDGER = [
    {
        "id": "E1",
        "tool": "diamond_blast_search",
        "args": {},
        "evidence": {
            "error": None,
            "result_summary": "hit",
            "raw_scores": {"hits": [{"uniprot_accession": "A1", "pident": 90.0}]},
            "source_ids": ["A1", "GO:0006915"],  # GO id present so the grounding check accepts it
            "source_urls": [],
        },
    }
]
FAKE_CANDIDATES = [{"go_id": "GO:0006915", "confidence": 0.8, "raw_score": 0.8, "evidence_ids": ["E1"], "explanations": ["x"]}]


def _run_dir_cleanup(report):
    shutil.rmtree(report["run_dir"], ignore_errors=True)


def _fake_quickgo_valid(go_id):
    return Evidence(
        tool="quickgo", query=go_id, result_summary="ok",
        raw_scores={"valid": True, "name": "apoptotic process", "aspect": "biological_process"},
        source_ids=[], source_urls=[],
    )


def _patched(client):
    return (
        patch("agent.loop.gather_evidence", return_value=FAKE_LEDGER),
        patch("agent.loop.aggregate", return_value=FAKE_CANDIDATES),
        patch("agent.context.AgentContext.build_exclusions", return_value=None),
        patch("agent.report.quickgo.run", side_effect=_fake_quickgo_valid),
    )


def test_happy_path_submits_on_first_attempt():
    client = FakeLLMClient([_final_answer_msg()])
    p1, p2, p3, p4 = _patched(client)
    with p1, p2, p3, p4:
        run = AgentRun(query_sequence="MEEP", query_accession="TEST1", eval_mode=False, client=client)
        report = run.run()

    try:
        assert report["stop_reason"] == "submitted final answer"
        assert report["recovered_from_text"] is False
        assert report["go_terms"][0]["go_id"] == "GO:0006915"
        assert report["token_usage"]["total"] == 150  # 1 chat call * 150
    finally:
        _run_dir_cleanup(report)


def test_plain_text_then_successful_retry():
    client = FakeLLMClient([_text_only_msg("I think it's a kinase."), _final_answer_msg()])
    p1, p2, p3, p4 = _patched(client)
    with p1, p2, p3, p4:
        run = AgentRun(query_sequence="MEEP", query_accession="TEST2", eval_mode=False, client=client)
        report = run.run()

    try:
        assert report["stop_reason"] == "submitted final answer"
        assert report["recovered_from_text"] is False
        no_tool_events = [e for e in run.trace if e["type"] == "no_tool_call"]
        assert len(no_tool_events) == 1
    finally:
        _run_dir_cleanup(report)


def test_plain_text_twice_recovers_from_text():
    client = FakeLLMClient([
        _text_only_msg("I believe this protein has GO:0006915 (E1) with high confidence 0.9."),
        _text_only_msg("Final answer: GO:0006915 (E1), confidence 0.9. Also GO:0005515 with no clear evidence."),
    ])
    p1, p2, p3, p4 = _patched(client)
    with p1, p2, p3, p4:
        run = AgentRun(query_sequence="MEEP", query_accession="TEST3", eval_mode=False, client=client)
        report = run.run()

    try:
        assert report["stop_reason"] == "recovered_from_text"
        assert report["recovered_from_text"] is True
        # GO:0006915 had "E1" on its line and is grounded (E1 is a real ledger id) -> kept
        assert any(t["go_id"] == "GO:0006915" for t in report["go_terms"])
        # GO:0005515 had no evidence id on its line -> ungrounded, dropped from go_terms
        assert all(t["go_id"] != "GO:0005515" for t in report["go_terms"])
    finally:
        _run_dir_cleanup(report)


def test_invalid_json_in_tool_call_retried_once():
    bad_call = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "submit_final_answer", "arguments": "{not valid json"}}]}
    client = FakeLLMClient([bad_call, _final_answer_msg()])
    p1, p2, p3, p4 = _patched(client)
    with p1, p2, p3, p4:
        run = AgentRun(query_sequence="MEEP", query_accession="TEST4", eval_mode=False, client=client)
        report = run.run()

    try:
        assert report["stop_reason"] == "submitted final answer"
        error_events = [e for e in run.trace if e["type"] == "tool_call_error"]
        assert len(error_events) == 1
    finally:
        _run_dir_cleanup(report)


def test_token_budget_exceeded_before_first_call():
    client = FakeLLMClient([_final_answer_msg()], budget_exceeded_from=0)
    p1, p2, p3, p4 = _patched(client)
    with p1, p2, p3, p4:
        run = AgentRun(query_sequence="MEEP", query_accession="TEST5", eval_mode=False, client=client)
        report = run.run()

    try:
        assert "MAX_TOKENS_PER_RUN" in report["stop_reason"]
        assert run.final_answer is None
        assert report["go_terms"] == []
    finally:
        _run_dir_cleanup(report)


def test_aggregator_candidates_included_in_report():
    client = FakeLLMClient([_final_answer_msg()])
    p1, p2, p3, p4 = _patched(client)
    with p1, p2, p3, p4:
        run = AgentRun(query_sequence="MEEP", query_accession="TEST6", eval_mode=False, client=client)
        report = run.run()

    try:
        assert report["aggregator_candidates"] == FAKE_CANDIDATES
    finally:
        _run_dir_cleanup(report)


def test_parse_go_terms_from_text_extracts_evidence_and_confidence():
    text = "This matches GO:0006915 (E1, E2) with confidence 0.85.\nAlso maybe GO:0005515 but unsure."
    terms = _parse_go_terms_from_text(text)
    by_id = {t["go_id"]: t for t in terms}
    assert by_id["GO:0006915"]["evidence_ids"] == ["E1", "E2"]
    assert by_id["GO:0006915"]["confidence"] == 0.85
    assert by_id["GO:0005515"]["evidence_ids"] == []
    assert by_id["GO:0005515"]["confidence"] == 0.5  # default when no number found


def test_parse_go_terms_from_text_empty_text():
    assert _parse_go_terms_from_text("") == []
    assert _parse_go_terms_from_text(None) == []
