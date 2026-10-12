"""OpenAI-style function-calling schema for the agent's final-answer step.

The agent no longer exposes its 8 underlying tools to the LLM as free-form
function calls -- evidence gathering is now deterministic
(agent/evidence_pipeline.py calls every tool directly, see its module
docstring for why). The only tool the LLM ever sees is this one: it reviews
the already-gathered evidence and the aggregator's scored candidates
(agent/aggregator.py), then must call this to submit its structured answer.
"""

SUBMIT_FINAL_ANSWER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit_final_answer",
        "description": (
            "Call this to submit your reviewed final answer. Every GO term and every claim in "
            "function_summary must cite an evidence ID (E1, E2, ...) from the evidence you were given."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "function_summary": {
                    "type": "string",
                    "description": "Short natural-language summary of the protein's likely function, citing evidence IDs in parentheses, e.g. 'Tumor suppressor (E2, E4).'",
                },
                "go_terms": {
                    "type": "array",
                    "description": "Predicted GO terms with confidence and supporting evidence IDs.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "go_id": {"type": "string"},
                            "confidence": {"type": "number", "description": "0.0 to 1.0"},
                            "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["go_id", "confidence", "evidence_ids"],
                    },
                },
                "disagreements_or_uncertainty": {
                    "type": "string",
                    "description": "Where evidence conflicted, was weak/hub-flagged, or you are not confident. Write 'None' if there is genuinely nothing to flag.",
                },
            },
            "required": ["function_summary", "go_terms", "disagreements_or_uncertainty"],
        },
    },
}
