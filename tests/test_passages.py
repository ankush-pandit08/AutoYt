import pytest

from autoyt.research import ClaimSelections, passages, select_claims


def test_passages_are_exact_source_substrings(research_data):
    _, lookup = passages(research_data.sources)
    assert lookup
    assert all(text in source.text for source, text in lookup.values())


def test_unknown_passage_selection_has_bounded_repair(research_data):
    for source in research_data.sources[:2]:
        source.text += " Memory holds data used by running programs."
    class InvalidLLM:
        calls = 0

        def structured(self, *args):
            self.calls += 1
            return ClaimSelections(claim_ids=["invented", "unknown"])

    llm = InvalidLLM()
    with pytest.raises(ValueError, match="two attempts"):
        select_claims(llm, research_data.sources)
    assert llm.calls == 2


def test_selection_copies_real_text_instead_of_model_quotations(research_data):
    first = research_data.claims[0].claim
    second = "Cloud Backup access was suspended as a protective measure."
    for source in research_data.sources[:2]:
        source.text += " " + second
    class SelectionLLM:
        def structured(self, *args):
            items = args[2]["candidates"]
            return ClaimSelections(claim_ids=[next(item["id"] for item in items if item["statement"] == text)
                                               for text in (first, second)])
    claims = select_claims(SelectionLLM(), research_data.sources)
    assert len(claims) == 2
    assert claims[1].evidence[0].quote == second
