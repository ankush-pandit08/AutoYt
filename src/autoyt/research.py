import hashlib
import re
from datetime import datetime, timezone
from functools import lru_cache
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit

import numpy as np
import snowballstemmer
import trafilatura
from pydantic import BaseModel, Field, create_model
from tld import get_fld

from .discovery import MAX_STORY_AGE_SECONDS, similarity, words
from .models import Claim, Evidence, Research, Source
from .store import write_json


@lru_cache(maxsize=2048)
def evidence_words(text):
    # Match grammatical variants, while quotation and number checks remain exact.
    ignored = {"we", "our", "their", "they", "them", "its", "was", "were", "has", "have", "had",
               "been", "being", "also", "however", "company", "said", "says", "utc", "remain", "remains",
               "january", "february", "march", "april", "may", "june", "july", "august",
               "september", "october", "november", "december"}
    return frozenset(snowballstemmer.stemmer("english").stemWords(sorted(words(text) - ignored)))


def publisher(url):
    host = urlsplit(url).hostname or ""
    # Conservative publisher families; subdomains and owned publications are not independent.
    for family, domains in {"bbc": ["bbc.com", "bbc.co.uk"],
                            "meta": ["meta.com", "fb.com"],
                            "google": ["google.com", "blog.google", "googleblog.com", "deepmind.google"],
                            "microsoft": ["microsoft.com", "github.com", "github.blog"],
                            "vox": ["theverge.com", "vox.com"],
                            "conde-nast": ["arstechnica.com", "wired.com"]}.items():
        if any(host == d or host.endswith("."+d) for d in domains):
            return family
    return get_fld(url, fail_silently=True) or host.removeprefix("www.")


def primary(url, cfg):
    host = urlsplit(url).hostname or ""
    return any(host == d or host.endswith("."+d) for d in cfg.primary_domains)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(v for k, v in attrs if k == "href" and v)


def official_links(html, base_url, title, cfg):
    """Prioritize cited announcement paths over site navigation and syndication feeds."""
    parser = Links()
    parser.feed(html)
    links = []
    for link in parser.links:
        url = urljoin(base_url, link).split("#")[0]
        parts = urlsplit(url)
        path = parts.path.strip("/")
        if parts.scheme != "https" or not primary(url, cfg) or not path:
            continue
        tokens = words(path.replace("-", " ").replace("_", " "))
        if tokens & {"rss", "feed", "xml", "login", "signin", "privacy", "terms"}:
            continue
        if len(path.split("/")) == 1 and path in {"news", "blog", "security", "research", "about"}:
            continue
        links.append(url)
    return sorted(dict.fromkeys(links),
                  key=lambda url: similarity(title, urlsplit(url).path.replace("-", " ")),
                  reverse=True)


def extract(url, html, cfg, date_hint=None, *, evergreen=False):
    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True)
    if not doc or not doc.text or len(doc.text) < 250:
        raise ValueError("Insufficient article text")
    if not doc.date and not date_hint and not evergreen:
        raise ValueError("Undated source rejected")
    date = datetime.fromisoformat(doc.date.replace("Z", "+00:00")) if doc.date else date_hint
    if date is not None and date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    current = datetime.now(timezone.utc)
    updated = None
    # Vendor advisory indexes can retain their original page creation date. Only use an
    # explicit, dated advisory heading near the top; preserve the original metadata date.
    if primary(url, cfg) and "advisori" in (doc.title or "").lower():
        match = re.search(r"(?:^|\n)([A-Z][a-z]+ \d{1,2}, \d{4}):[^\n]+", doc.text[:800])
        if match:
            updated = datetime.strptime(match.group(1), "%B %d, %Y").replace(tzinfo=timezone.utc)
    effective_date = updated or date
    if date is not None and date > current:
        raise ValueError("Stale or future source date")
    if not evergreen and not 0 <= (current-effective_date).total_seconds() <= MAX_STORY_AGE_SECONDS:
        raise ValueError("Stale or future source date")
    if date_hint and date and abs((date - date_hint).total_seconds()) > 86400 * 2:
        raise ValueError("Conflicting publication dates")
    return Source(url=url, title=doc.title or url, text=doc.text[:14000], published_at=date,
                  updated_at=updated, date_basis=("dated_advisory_heading" if updated else
                      "publication_metadata" if date else "current_document_no_publication_date"),
                  retrieved_at=current, primary=primary(url, cfg), publisher=publisher(url))


def independent_sources(sources, *, require_primary=True):
    accepted, hashes = [], set()
    for source in sources:
        hashed = hashlib.sha256(" ".join(source.text.lower().split()).encode()).hexdigest()
        if hashed in hashes or any(similarity(source.text, s.text) > .9 for s in accepted):
            continue
        hashes.add(hashed)
        accepted.append(source)
    if len({s.publisher for s in accepted}) < 2 or (require_primary and not any(s.primary for s in accepted)):
        raise ValueError("Need two independent sources including an official primary source")
    return accepted


class ClaimSelections(BaseModel):
    claim_ids: list[str] = Field(min_length=2, max_length=2)


class AtomicProposal(BaseModel):
    claim: str = Field(min_length=15, max_length=120, pattern=r"[.!?]$")
    pair_id: str


class AtomicProposals(BaseModel):
    claims: list[AtomicProposal] = Field(min_length=2, max_length=2)


def atomic_claims(llm, sources, topic, folder=None):
    """Extract shared atomic facts; copy evidence from retrieved text, never model-written quotes."""
    _, lookup = passages(sources)
    pairs = []
    def usable(text):
        return (len(text) <= 500 and text.endswith((".", "!", "?", '"', '”'))
                and not text.startswith(("|", "-", "•"))
                and not re.match(r"From time to time|This (?:advisory|update)|At \d", text)
                and not re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-|\b\d{1,3}(?:(?:\.|\[\.\])\d{1,3}){3}\b", text, re.I))
    semantic = {}
    if hasattr(llm, "embeddings") and getattr(getattr(llm, "cfg", None), "llm_provider", "ollama") == "ollama":
        eligible = [key for key, (s, text) in lookup.items() if usable(text)
                    and (not s.primary or int(key.rsplit("p", 1)[1]) < 25)][:160]
        vectors = np.asarray(llm.embeddings([lookup[key][1] for key in eligible]), dtype=float)
        if vectors.ndim != 2 or not np.isfinite(vectors).all():
            raise ValueError("Invalid local embedding vectors")
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        similarities = vectors @ vectors.T
        semantic = {(a, b): float(similarities[i, j]) for i, a in enumerate(eligible)
                    for j, b in enumerate(eligible)}
    topic_words = evidence_words(topic or "")
    for primary_id, (source, text) in lookup.items():
        position = int(primary_id.rsplit("p", 1)[1])
        if not source.primary or position >= 25 or not usable(text):
            continue
        for independent_id, (other, quote) in lookup.items():
            if source.publisher == other.publisher or not usable(quote):
                continue
            overlap = evidence_words(text) & evidence_words(quote)
            if len({w for w in overlap if not w.isdigit()}) >= 2:
                score = (.5 * len(overlap) / max(1, min(len(evidence_words(text)), len(evidence_words(quote))))
                         + .3 * min(1, len(overlap & topic_words) / 3) + .2 / (1 + position / 6))
                if semantic:
                    score = .85 * semantic.get((primary_id, independent_id), 0) + .15 * score
                pairs.append((score, primary_id, independent_id))
    pairs.sort(reverse=True)
    distinct = []
    seen_primary = set()
    for pair in pairs:
        if pair[1] not in seen_primary:
            distinct.append(pair)
            seen_primary.add(pair[1])
        if len(distinct) == 8:
            break
    def context(item):
        source, quote = item
        index = source.text.find(quote)
        line_start = source.text.rfind("\n", 0, index)
        previous_start = source.text.rfind("\n", 0, max(0, line_start)) + 1
        line_end = source.text.find("\n", index + len(quote))
        line_end = len(source.text) if line_end < 0 else line_end
        expanded = source.text[previous_start:line_end]
        return source, expanded if len(expanded) <= 700 else quote
    paired = {f"pair{i}": (context(lookup[a]), context(lookup[b])) for i, (_, a, b) in enumerate(distinct)}
    if not paired:
        raise ValueError("No overlapping primary and independent passages")
    documents = [{"id": key, "primary_quote": a[1], "independent_quote": b[1]}
                 for key, (a, b) in paired.items()]
    if folder:
        write_json(folder/"evidence_pairs.json", documents)
    accepted, rejected_pairs = [], set()
    feedback = None
    for _ in range(3):
        available = {key: pair for key, pair in paired.items() if key not in rejected_pairs}
        if not available:
            break
        proposal_schema = create_model("PairedAtomicProposal", claim=(str, Field(
            min_length=15, max_length=120, pattern=r"[.!?]$")), pair_id=(Literal[tuple(available)], ...))
        remaining = 2-len(accepted)
        schema = create_model("PairedAtomicProposals", claims=(
            list[proposal_schema], Field(min_length=remaining, max_length=remaining)))
        result = llm.structured(schema,
            "Extract the requested number of DIFFERENT concise factual sentences from the supplied evidence PAIRS. "
            "Every fact must be independently supported by BOTH quotations in its pair. "
            "Write ONE fact per sentence, at most TWELVE WORDS. End with a period. "
            "Focus on the central event or current service status. Preserve uncertainty and negation. "
            "No dates, times, counts, identifiers or causal chains. No product-name fragments. "
            "Do not append claims from only one quotation. Keep accepted claims unchanged and do not repeat them. "
            "Choose pair_id from the available pairs. The program copies the original exact quotations.",
            {"topic": topic, "pairs": [item for item in documents if item["id"] in available],
             "accepted_claims": [c.claim for c in accepted], "previous_failures": feedback})
        failures = []
        for proposal in result.claims:
            try:
                if not any(word[:1].islower() for word in proposal.claim.split()):
                    raise ValueError("Product-name fragment is not a complete fact")
                if proposal.pair_id not in available:
                    raise ValueError("Unknown evidence pair ID")
                (primary_source, primary_text), (other, other_text) = available[proposal.pair_id]
                claim = Claim(claim=proposal.claim, attribution=primary_source.publisher, evidence=[
                    Evidence(source_url=primary_source.url, quote=primary_text),
                    Evidence(source_url=other.url, quote=other_text)])
                verify_claims([claim], sources)
                if any(similarity(claim.claim, old.claim) > .85 for old in accepted):
                    raise ValueError("Choose a different fact from the accepted claim")
                accepted.append(claim)
            except ValueError as error:
                failures.append({"pair_id": proposal.pair_id, "claim": proposal.claim, "reason": str(error)})
                rejected_pairs.add(proposal.pair_id)
        if len(accepted) == 2:
            if folder:
                write_json(folder/"atomic_claims.json", [c.model_dump() for c in accepted])
            return accepted
        feedback = failures
        if folder:
            write_json(folder/"atomic_failure.json", {"accepted": [c.model_dump() for c in accepted],
                                                     "failures": failures})
    raise ValueError("Atomic claims failed exact evidence checks after three bounded attempts")


def passages(sources):
    documents, lookup = [], {}
    for index, source in enumerate(sources):
        items = []
        for sentence in re.split(r'(?<=[.!?])\s+(?=[A-Z"\u201c])|\n+', source.text):
            sentence = sentence.strip()
            if not 25 <= len(sentence) <= 700:
                continue
            key = f"s{index}p{len(items)}"
            lookup[key] = (source, sentence)
            items.append({"id": key, "text": sentence})
        documents.append({"publisher": source.publisher, "primary": source.primary,
                          "passages": items})
    return documents, lookup


def corroborated_candidates(sources):
    _, lookup = passages(sources)
    candidates = []
    for source, text in lookup.values():
        if not source.primary or len(text) > 400 or text[-1] not in '.!\u201d"':
            continue
        if text.startswith(("-", "•")) or re.search(r"\b\d{1,3}(?:(?:\.|\[\.\])\d{1,3}){3}\b", text):
            continue
        if re.match(r"(?:It|They|This|These|Those)\b", text) or re.search(
                r"GUID order|alphabetical targeting|new secrets|MSP management application|security tool's application|IP in .*telemetry", text, re.I):
            continue
        matches = []
        for other, quote in lookup.values():
            if other.publisher == source.publisher:
                continue
            coverage = len(evidence_words(text) & evidence_words(quote))/max(1, len(evidence_words(text)))
            if coverage < .65:
                continue
            candidate = Claim(claim=text, attribution=source.publisher, evidence=[
                Evidence(source_url=source.url, quote=text), Evidence(source_url=other.url, quote=quote)])
            try:
                verify_claims([candidate], sources)
            except ValueError:
                continue
            matches.append((coverage, candidate))
        if matches and not any(c.claim == text for c in candidates):
            candidates.append(max(matches, key=lambda m: m[0])[1])
    return candidates[:16]


def select_claims(llm, sources, folder=None, topic=None, *, include_primary=False):
    # Prefer the newest first-party update over older quoted incident details.
    latest = max((s for s in sources if s.primary), key=lambda s: s.updated_at or s.published_at or s.retrieved_at)
    current_sources = [latest, *[s for s in sources if s.publisher != latest.publisher
                                and (include_primary or not s.primary)]]
    candidates = corroborated_candidates(current_sources)
    if len(candidates) < 2:
        return atomic_claims(llm, current_sources, topic, folder)
    lookup = {f"c{i}": claim for i, claim in enumerate(candidates)}
    schema = create_model("GroundedClaimSelections", claim_ids=(
        list[Literal[tuple(lookup)]], Field(min_length=2, max_length=2)))
    if folder:
        write_json(folder/"corroborated_candidates.json", {k: c.model_dump() for k, c in lookup.items()})
    feedback = None
    for _ in range(2):
        selection = llm.structured(schema,
            "Choose exactly TWO DIFFERENT claim IDs relevant to the topic. The statements are verbatim "
            "primary-source extracts already checked for independent corroboration. Prefer the core "
            "event and current response/investigation. Avoid operational instructions, indicators, "
            "unrelated product details and unqualified reassurance. Return only the two IDs. "
            "Do not invent or rewrite statements. Choose distinct facts, not near duplicates.",
            {"topic": topic, "candidates": [{"id": k, "statement": c.claim, "attribution": c.attribution}
                                          for k, c in lookup.items()],
             "previous_validation_failure": feedback})
        if folder:
            write_json(folder/"claim_selections.json", selection.model_dump())
        try:
            if len(set(selection.claim_ids)) != 2 or any(key not in lookup for key in selection.claim_ids):
                raise ValueError("Choose two different known claim IDs")
            claims = [lookup[key] for key in selection.claim_ids]
            verify_claims(claims, sources)
            return claims
        except ValueError as error:
            feedback = str(error)
    raise ValueError("Claim selection failed exact evidence checks after two attempts")


def verify_claims(claims, sources, *, require_primary=True, minimum_overlap=.65):
    lookup = {s.url: s for s in sources}
    for claim in claims:
        families = set()
        has_primary = False
        for evidence in claim.evidence:
            source = lookup.get(evidence.source_url)
            if source is None:
                raise ValueError("Unknown claim source")
            quote = " ".join(evidence.quote.lower().split())
            if quote not in " ".join(source.text.lower().split()):
                raise ValueError("Evidence quotation absent from retrieved document")
            cw = evidence_words(claim.claim)
            if len(cw & evidence_words(quote)) / max(1, len(cw)) < minimum_overlap:
                raise ValueError("Claim exceeds quoted evidence from " + source.publisher)
            for number in re.findall(r"\b\d+(?:[.,]\d+)*\b", claim.claim):
                if number not in quote:
                    raise ValueError("Numeric claim lacks independent support")
            families.add(source.publisher)
            has_primary |= source.primary
        if len(families) < 2 or (require_primary and not has_primary):
            raise ValueError("Each claim needs primary and independent corroboration")


def publication_hint(topic):
    # A Trends timestamp dates search interest, not publication of its linked news article.
    return None if getattr(topic, "content_kind", "news") == "evergreen" or urlsplit(
        getattr(topic, "source", "")).hostname == "trends.google.com" else topic.published_at


def research(cfg, web, llm, topic, candidates, folder=None):
    evergreen = getattr(topic, "content_kind", "news") == "evergreen"
    urls = [(topic.url, publication_hint(topic)), *[(u, None) for u in topic.related_urls if u != topic.url]]
    if not evergreen:
        urls.extend((t.url, publication_hint(t)) for t in candidates
                    if t.content_kind == "news" and t.url != topic.url and similarity(topic.title, t.title) >= .35)
    sources, seen = [], set()
    retrievals = []
    index = 0
    while index < len(urls) and index < 16 and len(sources) < 6:
        url, hint = urls[index]
        index += 1
        if url in seen:
            continue
        seen.add(url)
        try:
            html = web.article(url)
            source = extract(url, html, cfg, hint, evergreen=True) if evergreen else extract(url, html, cfg, hint)
            threshold = .25 if source.primary else .4
            if similarity(topic.title, source.title + " " + source.text) < threshold:
                retrievals.append({"url": url, "result": "Article is not sufficiently related to this story"})
                continue
            sources.append(source)
            retrievals.append({"url": url, "result": "Accepted official source" if source.primary else "Accepted independent source"})
            # Follow cited official announcements, never arbitrary links across the web.
            if not evergreen:
                urls.extend((link, None) for link in official_links(html, url, topic.title, cfg)
                            if link not in seen and not any(link == pending for pending, _ in urls))
        except Exception as error:
            # Persist only controlled diagnostics; HTTP exception strings may contain credentials.
            import httpx
            if isinstance(error, httpx.HTTPStatusError):
                reason = f"Source returned HTTP {error.response.status_code}"
            elif type(error) is ValueError and str(error) in (
                    "Insufficient article text", "Undated source rejected", "Stale or future source date",
                    "Conflicting publication dates", "Source disallows automated retrieval",
                    "Cannot establish article access permission"):
                reason = str(error)
            else:
                reason = "Source retrieval unavailable (" + type(error).__name__ + ")"
            retrievals.append({"url": url, "result": reason})
            continue
    if folder:
        write_json(folder/"source_retrievals.json", retrievals)
    try:
        accepted = independent_sources(sources)
    except ValueError:
        primary_count = sum(source.primary for source in sources)
        independent_count = len({source.publisher for source in sources if not source.primary})
        raise ValueError(f"Evidence incomplete: {primary_count} official and {independent_count} independent "
                         "sources accepted; need two independent publishers including a recent official source") from None
    sources, families = [], set()
    for source in sorted(accepted, key=lambda s: (s.primary, s.updated_at or s.published_at or s.retrieved_at), reverse=True):
        if source.publisher not in families:
            sources.append(source)
            families.add(source.publisher)
        if len(sources) == 6:
            break
    if folder:
        write_json(folder/"source_documents.json", [s.model_dump(mode="json") for s in sources])
    claims = select_claims(llm, sources, folder, topic.title, include_primary=True) if evergreen else select_claims(llm, sources, folder, topic.title)
    from .ai import Audit
    audit = llm.structured(Audit,
        "Independently audit these claims against retrieved documents. Reject contradictions, stale "
        "information, missing context, misleading comparison, unsupported certainty or numerical claims. "
        "Audit the supplied claims, not unrelated assertions elsewhere in the documents. "
        "Each claim must be individually established by BOTH its primary quotation and at least one "
        "independent quotation. Related subject matter is not corroboration. Use context only to resolve "
        "references. The evidence quotations have already been checked as exact substrings of the FULL "
        "retrieved documents. current_context is only a SHORT excerpt and may omit those quotations; "
        "do not reject a quotation merely because it is outside that excerpt. Evaluate whether each "
        "claim is entailed by its supplied evidence quotations. Preserve "
        "uncertainty, negation and event dates. If supported, return reasons=[]. "
        "For rolling vendor advisories, updated_at with date_basis=dated_advisory_heading identifies the current "
        "dated update; published_at may be the older index-page creation date. "
        "supported must be false if any material claim is not entailed by its quotations.",
        {"claims": [c.model_dump() for c in claims], "sources": [
            {**s.model_dump(exclude={"text"}), "current_context": s.text[:700] if s.primary else s.text[:300]}
            for s in sources]})
    if folder:
        write_json(folder/"source_audit.json", audit.model_dump())
    if not audit.supported:
        raise ValueError("Source evidence audit failed")
    return Research(sources=sources, claims=claims)
