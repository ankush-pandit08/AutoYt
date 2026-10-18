import pytest

from autoyt.research import AtomicProposals, ClaimSelections, atomic_claims, passages, select_claims


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


def test_atomic_claims_copy_exact_passages_and_verify_shared_scope(research_data):
    research_data.sources[0].text = (
        "The vendor suspended Cloud Backup and revoked its certificates during the investigation. "
        "A specialist forensics firm was engaged by the vendor to investigate the incident.")
    research_data.sources[1].text = (
        "Cloud Backup was suspended after unauthorized access was reported. "
        "The vendor engaged a specialist forensics firm to investigate the breach.")
    class Model:
        def structured(self, schema, instruction, data):
            docs = data["pairs"]
            def key(contains):
                return next(d["id"] for d in docs if contains in d["primary_quote"] and contains in d["independent_quote"])
            return AtomicProposals(claims=[
                {"claim": "Cloud Backup was suspended.", "pair_id": key("suspended")},
                {"claim": "The vendor engaged a specialist forensics firm.", "pair_id": key("forensics")},
            ])
    claims = atomic_claims(Model(), research_data.sources[:2], "Cloud Backup incident")
    assert len(claims) == 2
    assert all(e.quote in next(s.text for s in research_data.sources if s.url == e.source_url)
               for c in claims for e in c.evidence)


def test_indicators_and_contextless_pronouns_are_not_headline_claims(research_data):
    from autoyt.research import corroborated_candidates
    text = "It was used from 144.172.64[.]128 to obtain customer tokens. "
    text += "The actor moved through tenants in sorted GUID order."
    for source in research_data.sources[:2]:
        source.text = text
    assert corroborated_candidates(research_data.sources[:2]) == []


def test_latest_primary_update_takes_precedence(research_data):
    from datetime import timedelta
    current = research_data.sources[0].published_at
    newer = research_data.sources[0].model_copy(update={
        "url": "https://vendor.example/advisory", "publisher": "vendor",
        "updated_at": current,
        "text": "Cloud Backup remains suspended during the investigation. "
                "ConnectWise has not announced a restoration time for Cloud Backup."})
    old = research_data.sources[0].model_copy(update={"published_at": current-timedelta(days=2)})
    independent = research_data.sources[1].model_copy(update={"text": newer.text + " " + old.text})
    class Model:
        def structured(self, schema, instruction, data):
            assert all(item["attribution"] == "vendor" for item in data["candidates"])
            return ClaimSelections(claim_ids=["c0", "c1"])
    claims = select_claims(Model(), [old, newer, independent])
    assert all(claim.evidence[0].source_url == newer.url for claim in claims)
