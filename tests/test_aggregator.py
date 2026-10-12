from agent.aggregator import aggregate


def _entry(eid, tool, args, raw_scores, error=None):
    return {"id": eid, "tool": tool, "args": args, "evidence": {"error": error, "raw_scores": raw_scores}}


def test_interpro_domain_gives_full_weight():
    ledger = [
        _entry("E1", "interproscan_scan", {}, {"domains": [{"interpro_id": "IPR011615", "name": "p53 domain", "go_ids": ["GO:0000976"]}]}),
    ]
    result = aggregate(ledger)
    assert result[0]["go_id"] == "GO:0000976"
    assert result[0]["confidence"] == 1.0
    assert result[0]["evidence_ids"] == ["E1"]


def test_diamond_homolog_weighted_by_identity():
    ledger = [
        _entry("E1", "diamond_blast_search", {}, {"hits": [{"uniprot_accession": "A1", "pident": 80.0}]}),
        _entry("E2", "homolog_go_annotations", {"source": "diamond"}, {"go_terms": [{"go_id": "GO:0001", "evidence_code": "IDA:UniProtKB", "source_accession": "A1"}]}),
    ]
    result = aggregate(ledger)
    assert result[0]["go_id"] == "GO:0001"
    assert result[0]["raw_score"] == 0.8  # 0.80 identity * 1.0 (IDA = experimental)


def test_diamond_homolog_discounted_for_non_experimental_evidence():
    ledger = [
        _entry("E1", "diamond_blast_search", {}, {"hits": [{"uniprot_accession": "A1", "pident": 80.0}]}),
        _entry("E2", "homolog_go_annotations", {"source": "diamond"}, {"go_terms": [{"go_id": "GO:0001", "evidence_code": "IEA:Ensembl", "source_accession": "A1"}]}),
    ]
    result = aggregate(ledger)
    assert result[0]["raw_score"] == 0.48  # 0.80 * 0.6 (not an experimental code)


def test_protrek_is_heavily_discounted_vs_diamond():
    ledger = [
        _entry("E1", "diamond_blast_search", {}, {"hits": [{"uniprot_accession": "A1", "pident": 80.0}]}),
        _entry("E2", "homolog_go_annotations", {"source": "diamond"}, {"go_terms": [{"go_id": "GO:0001", "evidence_code": "IDA", "source_accession": "A1"}]}),
        _entry("E3", "protrek_retrieve", {}, {"ranked": [{"accession": "A2", "description": "d", "score": 1.0}]}),
        _entry("E4", "homolog_go_annotations", {"source": "protrek"}, {"go_terms": [{"go_id": "GO:0002", "evidence_code": "IDA", "source_accession": "A2"}]}),
    ]
    result = aggregate(ledger)
    by_id = {r["go_id"]: r["raw_score"] for r in result}
    assert by_id["GO:0001"] > by_id["GO:0002"]
    assert by_id["GO:0002"] == 0.25


def test_esm2_hub_flagged_neighbour_down_weighted():
    ledger = [
        _entry("E1", "esm2_knn_search", {}, {"neighbours": [
            {"accession": "HUB1", "cosine_similarity": 0.9, "is_hub": True},
            {"accession": "OK1", "cosine_similarity": 0.9, "is_hub": False},
        ]}),
        _entry("E2", "homolog_go_annotations", {"source": "esm2"}, {"go_terms": [
            {"go_id": "GO:0003", "evidence_code": "IDA", "source_accession": "HUB1"},
            {"go_id": "GO:0004", "evidence_code": "IDA", "source_accession": "OK1"},
        ]}),
    ]
    result = aggregate(ledger)
    by_id = {r["go_id"]: r["raw_score"] for r in result}
    assert by_id["GO:0004"] > by_id["GO:0003"]
    assert "HUB-FLAGGED" in result[[r["go_id"] for r in result].index("GO:0003")]["explanations"][0]


def test_corroborating_sources_add_up_and_cap_at_one():
    ledger = [
        _entry("E1", "interproscan_scan", {}, {"domains": [{"interpro_id": "IPR1", "name": "x", "go_ids": ["GO:0005"]}]}),
        _entry("E2", "diamond_blast_search", {}, {"hits": [{"uniprot_accession": "A1", "pident": 100.0}]}),
        _entry("E3", "homolog_go_annotations", {"source": "diamond"}, {"go_terms": [{"go_id": "GO:0005", "evidence_code": "IDA", "source_accession": "A1"}]}),
    ]
    result = aggregate(ledger)
    assert result[0]["go_id"] == "GO:0005"
    assert result[0]["raw_score"] == 2.0  # 1.0 (domain) + 1.0 (100% identity diamond)
    assert result[0]["confidence"] == 1.0  # capped
    assert set(result[0]["evidence_ids"]) == {"E1", "E3"}


def test_errored_evidence_entries_ignored():
    ledger = [
        _entry("E1", "diamond_blast_search", {}, {}, error="timeout"),
        _entry("E2", "interproscan_scan", {}, {"domains": [{"interpro_id": "IPR1", "name": "x", "go_ids": ["GO:0006"]}]}),
    ]
    result = aggregate(ledger)
    assert len(result) == 1
    assert result[0]["go_id"] == "GO:0006"


def test_empty_ledger_returns_empty_list():
    assert aggregate([]) == []
