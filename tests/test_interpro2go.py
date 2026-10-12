from tools import interpro2go


def test_get_go_terms_known_entry():
    # IPR011615 (p53 DNA-binding domain) -> GO:0000976, verified live against
    # the real mapping file and cross-checked against InterProScan's own
    # goXRefs for the same entry (see docs/TOOLS.md tool #3).
    assert interpro2go.get_go_terms("IPR011615") == ["GO:0000976"]


def test_get_go_terms_entry_with_multiple_terms():
    terms = interpro2go.get_go_terms("IPR000003")
    assert "GO:0003677" in terms
    assert "GO:0006355" in terms


def test_get_go_terms_unknown_entry_returns_empty():
    assert interpro2go.get_go_terms("IPR_DOES_NOT_EXIST") == []
