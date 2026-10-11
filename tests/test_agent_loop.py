import json
import shutil
from unittest.mock import patch

from agent.loop import AgentRun
from tools.evidence import Evidence


class FakeTokenUsage:
    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0

    @property
    def total(self):
        return self.input_tokens + self.output_tokens


class FakeLLMClient:
    """Scripted replacement for agent.llm_client.LLMClient -- returns a fixed
    sequence of assistant messages, one per .chat() call, so the loop's
    control flow can be tested without a live Ollama/DeepSeek/Qwen server."""

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


def _tool_call_msg(name, args, call_id="call_1"):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def _final_answer_msg(call_id="call_2"):
    args = {
        "function_summary": "A test protein (E1).",
        "go_terms": [{"go_id": "GO:0006915", "confidence": 0.8, "evidence_ids": ["E1"]}],
        "disagreements_or_uncertainty": "None",
    }
    return _tool_call_msg("submit_final_answer", args, call_id)


def _run_dir_cleanup(report):
    shutil.rmtree(report["run_dir"], ignore_errors=True)


def test_happy_path_one_tool_call_then_final_answer():
    fake_evidence = Evidence(tool="diamond_blast", query="q", result_summary="1 hit", raw_scores={"hits": []}, source_ids=["X1", "GO:0006915"], source_urls=[])
    client = FakeLLMClient([
        _tool_call_msg("diamond_blast_search", {"sequence": "MEEP"}),
        _final_answer_msg(),
    ])

    with patch("agent.loop.dispatch", return_value=fake_evidence), patch(
        "agent.dispatch.AgentContext.build_exclusions", return_value=None
    ), patch("agent.report.quickgo.run") as mock_quickgo:
        mock_quickgo.return_value = Evidence(tool="quickgo", query="GO:0006915", result_summary="ok", raw_scores={"valid": True, "name": "apoptotic process", "aspect": "biological_process"}, source_ids=[], source_urls=[])
        run = AgentRun(query_sequence="MEEP", query_accession="TEST1", eval_mode=False, max_steps=5, client=client)
        report = run.run()

    try:
        assert report["stop_reason"] == "submitted final answer"
        assert len(run.ledger) == 1
        assert run.ledger[0]["id"] == "E1"
        assert report["go_terms"][0]["go_id"] == "GO:0006915"
        assert report["go_terms"][0]["name"] == "apoptotic process"
        assert report["token_usage"]["total"] == 300  # 2 chat calls * 150
    finally:
        _run_dir_cleanup(report)


def test_ungrounded_go_term_flagged_separately_not_fabricated_as_valid():
    """Regression test for a real bug found on a live run: human beta-actin
    (P60709) got GO:0005576 'extracellular region' and GO:0006973
    'intracellular accumulation of glycerol' reported at 0.95 confidence --
    both are valid, non-obsolete GO IDs (so QuickGO validity alone would
    accept them), but neither ever appeared in any tool's raw output. The
    fix in agent/report.py cross-checks every GO ID against the set of GO
    IDs that actually appeared in the evidence ledger's tool outputs."""
    fake_evidence = Evidence(
        tool="interproscan",
        query="q",
        result_summary="actin domain",
        raw_scores={},
        source_ids=["IPR004000", "GO:0005737"],  # GO:0005737 = cytoplasm -- the grounded one
        source_urls=[],
    )
    args = {
        "function_summary": "Actin (E1).",
        "go_terms": [
            {"go_id": "GO:0005737", "confidence": 0.9, "evidence_ids": ["E1"]},  # grounded
            {"go_id": "GO:0005576", "confidence": 0.95, "evidence_ids": ["E1"]},  # NOT grounded
        ],
        "disagreements_or_uncertainty": "None",
    }
    client = FakeLLMClient([
        _tool_call_msg("interproscan_scan", {"sequence": "MEEP"}),
        _tool_call_msg("submit_final_answer", args),
    ])

    with patch("agent.loop.dispatch", return_value=fake_evidence), patch(
        "agent.dispatch.AgentContext.build_exclusions", return_value=None
    ), patch("agent.report.quickgo.run") as mock_quickgo:
        mock_quickgo.return_value = Evidence(
            tool="quickgo", query="g", result_summary="ok",
            raw_scores={"valid": True, "name": "n", "aspect": "a"}, source_ids=[], source_urls=[],
        )
        run = AgentRun(query_sequence="MEEP", query_accession="TEST7", eval_mode=False, max_steps=5, client=client)
        report = run.run()

    try:
        assert [t["go_id"] for t in report["go_terms"]] == ["GO:0005737"]
        assert [t["go_id"] for t in report["go_terms_ungrounded"]] == ["GO:0005576"]
        assert "not evidence-grounded" in report["go_terms_ungrounded"][0]["reason"]
    finally:
        _run_dir_cleanup(report)


def test_invalid_json_arguments_fed_back_not_crashed():
    client = FakeLLMClient([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "diamond_blast_search", "arguments": "{not valid json"}}]},
        _final_answer_msg(),
    ])

    with patch("agent.dispatch.AgentContext.build_exclusions", return_value=None), patch(
        "agent.report.quickgo.run"
    ) as mock_quickgo:
        mock_quickgo.return_value = Evidence(tool="quickgo", query="g", result_summary="ok", raw_scores={"valid": True, "name": "n", "aspect": "a"}, source_ids=[], source_urls=[])
        run = AgentRun(query_sequence="MEEP", query_accession="TEST2", eval_mode=False, max_steps=5, client=client)
        report = run.run()

    try:
        assert len(run.ledger) == 0  # the invalid call never produced evidence
        error_events = [e for e in run.trace if e["type"] == "tool_call_error"]
        assert len(error_events) == 1
        assert "Invalid JSON" in error_events[0]["error"]
    finally:
        _run_dir_cleanup(report)


def test_schema_violation_fed_back_not_crashed():
    client = FakeLLMClient([
        _tool_call_msg("diamond_blast_search", {"top_n": 5}),  # missing required "sequence"
        _final_answer_msg(),
    ])

    with patch("agent.dispatch.AgentContext.build_exclusions", return_value=None), patch(
        "agent.report.quickgo.run"
    ) as mock_quickgo:
        mock_quickgo.return_value = Evidence(tool="quickgo", query="g", result_summary="ok", raw_scores={"valid": True, "name": "n", "aspect": "a"}, source_ids=[], source_urls=[])
        run = AgentRun(query_sequence="MEEP", query_accession="TEST3", eval_mode=False, max_steps=5, client=client)
        report = run.run()

    try:
        error_events = [e for e in run.trace if e["type"] == "tool_call_error"]
        assert len(error_events) == 1
        assert "schema validation" in error_events[0]["error"]
    finally:
        _run_dir_cleanup(report)


def test_max_steps_reached_without_final_answer():
    always_tool_call = _tool_call_msg("diamond_blast_search", {"sequence": "MEEP"})
    fake_evidence = Evidence(tool="diamond_blast", query="q", result_summary="hit", raw_scores={}, source_ids=[], source_urls=[])
    client = FakeLLMClient([always_tool_call] * 10)

    with patch("agent.loop.dispatch", return_value=fake_evidence), patch(
        "agent.dispatch.AgentContext.build_exclusions", return_value=None
    ):
        run = AgentRun(query_sequence="MEEP", query_accession="TEST4", eval_mode=False, max_steps=3, client=client)
        report = run.run()

    try:
        assert "MAX_STEPS" in report["stop_reason"]
        assert report["go_terms"] == []
        assert run.final_answer is None
    finally:
        _run_dir_cleanup(report)


def _text_only_msg(text):
    return {"role": "assistant", "content": text, "tool_calls": []}


def test_plain_text_final_answer_stops_early_with_unvalidated_fallback():
    """Regression test for a real bug found when running this agent against
    qwen3:8b via Ollama: after one real tool call, the model kept writing its
    final answer as plain text ('**submit_final_answer** ... Final Answer: ...')
    instead of actually calling the submit_final_answer function -- even with
    tool_choice='required' (verified live, Ollama still ignored it). Without
    this fix the loop burned all 10 steps repeating the same text. The fix:
    stop after 2 consecutive no-tool-call turns and surface the last text as
    a clearly UNVALIDATED fallback, never silently burning the step budget
    and never fabricating GO terms from unstructured text."""
    client = FakeLLMClient([
        _text_only_msg("Final Answer: this protein is a tumor suppressor."),
        _text_only_msg("Final Answer: this protein is a tumor suppressor."),
        _final_answer_msg(),  # would never be reached -- loop must stop at turn 2
    ])

    with patch("agent.dispatch.AgentContext.build_exclusions", return_value=None):
        run = AgentRun(query_sequence="MEEP", query_accession="TEST6", eval_mode=False, max_steps=10, client=client)
        report = run.run()

    try:
        assert report["steps_used"] == 2  # stopped after 2 attempts, not all 10
        assert "did not use tool-calling" in report["stop_reason"]
        assert "[UNVALIDATED" in report["function_summary"]
        assert "tumor suppressor" in report["function_summary"]
        assert report["go_terms"] == []  # never fabricate GO terms from free text
        no_tool_events = [e for e in run.trace if e["type"] == "no_tool_call"]
        assert len(no_tool_events) == 2
    finally:
        _run_dir_cleanup(report)


def test_token_budget_exceeded_stops_immediately():
    client = FakeLLMClient([_final_answer_msg()], budget_exceeded_from=0)

    with patch("agent.dispatch.AgentContext.build_exclusions", return_value=None):
        run = AgentRun(query_sequence="MEEP", query_accession="TEST5", eval_mode=False, max_steps=5, client=client)
        report = run.run()

    try:
        assert "MAX_TOKENS_PER_RUN" in report["stop_reason"]
        assert run.final_answer is None
    finally:
        _run_dir_cleanup(report)
