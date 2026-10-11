"""OpenAI-style function-calling schemas for every tool the agent can call,
plus the `submit_final_answer` pseudo-tool that ends the loop with structured
output (far more reliable to parse than free text, especially with a small
local model). Only model-controllable arguments are exposed here -- context
values the model must never set (EBI_CONTACT_EMAIL, eval-mode exclusion
lists) are injected by agent/dispatch.py, not by the model.

UNIPROT_SCHEMA is kept separate so agent/loop.py can omit it entirely in
evaluation mode (CLAUDE.md: UniProt is the ground-truth source and must not
be callable by the agent during evaluation).
"""

UNIPROT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "uniprot_lookup",
        "description": "Fetch curated UniProt annotation (protein name, organism, GO terms, function text) for a UniProt accession. NOT available during evaluation runs.",
        "parameters": {
            "type": "object",
            "properties": {"accession": {"type": "string", "description": "UniProt accession, e.g. P04637"}},
            "required": ["accession"],
        },
    },
}

QUICKGO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "quickgo_validate",
        "description": "Check whether a GO (Gene Ontology) term ID is valid and not obsolete, and get its name, aspect, and ancestor terms. Use this to validate any GO ID before including it in your final answer.",
        "parameters": {
            "type": "object",
            "properties": {"go_id": {"type": "string", "description": "GO term ID, e.g. GO:0006915"}},
            "required": ["go_id"],
        },
    },
}

INTERPROSCAN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "interproscan_scan",
        "description": "Find protein domains/families (InterPro entries) in an amino-acid sequence, with GO cross-references. STRONG evidence -- domain matches are specific and reliable. Slow (async job, often 1-10 minutes).",
        "parameters": {
            "type": "object",
            "properties": {"sequence": {"type": "string", "description": "amino-acid sequence"}},
            "required": ["sequence"],
        },
    },
}

ALPHAFOLD_FOLDSEEK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "alphafold_foldseek_search",
        "description": "Fetch a UniProt accession's AlphaFold-predicted 3D structure and search for structurally similar proteins (Foldseek). Can find functional similarity even when sequence similarity is low.",
        "parameters": {
            "type": "object",
            "properties": {
                "accession": {"type": "string", "description": "UniProt accession, e.g. P04637"},
                "database": {"type": "string", "description": "Foldseek database to search", "default": "afdb-swissprot"},
            },
            "required": ["accession"],
        },
    },
}

DIAMOND_BLAST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "diamond_blast_search",
        "description": "Search a local Swiss-Prot database for sequence homologs (DIAMOND/BLAST). STRONG evidence -- high sequence identity to a well-annotated homolog is one of the most reliable function-transfer signals.",
        "parameters": {
            "type": "object",
            "properties": {
                "sequence": {"type": "string", "description": "amino-acid sequence"},
                "top_n": {"type": "integer", "description": "number of top hits to return", "default": 10},
            },
            "required": ["sequence"],
        },
    },
}

PROTREK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "protrek_retrieve",
        "description": (
            "Retrieve the best-matching natural-language function descriptions for a sequence from a pool of "
            "5,000 real Swiss-Prot annotations (ProTrek trimodal embedding search). "
            "WEAK evidence ONLY -- a high score often means a pathway member or interaction partner "
            "(e.g. something that regulates or binds the query protein), NOT necessarily the query's own "
            "function. Never treat a ProTrek hit as proof of the query's function by itself; corroborate with "
            "domain (InterProScan) or homology (DIAMOND) evidence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sequence": {"type": "string", "description": "amino-acid sequence"},
                "top_k": {"type": "integer", "description": "number of top matches to return", "default": 10},
            },
            "required": ["sequence"],
        },
    },
}

ESM2_KNN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "esm2_knn_search",
        "description": (
            "Find nearest-neighbour proteins by ESM-2 embedding similarity in a 5,000-protein Swiss-Prot "
            "subset, with a hubness diagnostic (k-occurrence) per neighbour. "
            "The cosine-similarity values are a RANKING ONLY, never an absolute confidence score -- do not "
            "quote them as a probability or percentage. If a neighbour's `is_hub` field is true, it is an "
            "over-popular point that shows up as a 'nearest neighbour' for many unrelated queries too -- "
            "down-weight that neighbour's evidence and say so explicitly in your final answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sequence": {"type": "string", "description": "amino-acid sequence"},
                "top_k": {"type": "integer", "description": "number of neighbours to return", "default": 10},
            },
            "required": ["sequence"],
        },
    },
}

SUBMIT_FINAL_ANSWER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit_final_answer",
        "description": (
            "Call this ONLY when you are done gathering evidence and ready to give your final answer. "
            "This ends the session. Every GO term and every claim in function_summary must be traceable to "
            "an evidence ID (E1, E2, ...) from a tool call you actually made."
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
                    "description": "Where tools disagreed, where evidence was weak/hub-flagged, or where you are not confident. Write 'None' if there is genuinely nothing to flag.",
                },
            },
            "required": ["function_summary", "go_terms", "disagreements_or_uncertainty"],
        },
    },
}

# name -> schema, for building the tools list and for jsonschema validation
ALL_SCHEMAS = {
    "uniprot_lookup": UNIPROT_SCHEMA,
    "quickgo_validate": QUICKGO_SCHEMA,
    "interproscan_scan": INTERPROSCAN_SCHEMA,
    "alphafold_foldseek_search": ALPHAFOLD_FOLDSEEK_SCHEMA,
    "diamond_blast_search": DIAMOND_BLAST_SCHEMA,
    "protrek_retrieve": PROTREK_SCHEMA,
    "esm2_knn_search": ESM2_KNN_SCHEMA,
    "submit_final_answer": SUBMIT_FINAL_ANSWER_SCHEMA,
}


def build_tools_list(eval_mode: bool) -> list[dict]:
    names = [n for n in ALL_SCHEMAS if not (eval_mode and n == "uniprot_lookup")]
    return [ALL_SCHEMAS[n] for n in names]
