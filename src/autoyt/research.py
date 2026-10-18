import hashlib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit

import trafilatura
from pydantic import BaseModel, Field, create_model
from tld import get_fld

from .discovery import similarity, words
from .models import Claim, Evidence, Research, Source
from .store import write_json


def publisher(url):
    host = urlsplit(url).hostname or ""
    # Conservative publisher families; subdomains and owned publications are not independent.
    for family, domains in {"google": ["google.com", "blog.google", "googleblog.com"],
                            "microsoft": ["microsoft.com"],
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


def extract(url, html, cfg, date_hint=None):
    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True)
    if not doc or not doc.text or len(doc.text) < 250:
        raise ValueError("Insufficient article text")
    if not doc.date and not date_hint:
        raise ValueError("Undated source rejected")
    date = datetime.fromisoformat(doc.date.replace("Z", "+00:00")) if doc.date else date_hint
    if date.tzinfo is None:
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
    if date > current or effective_date > current or (current-effective_date).days > 7:
        raise ValueError("Stale or future source date")
    if date_hint and abs((date - date_hint).total_seconds()) > 86400 * 2:
        raise ValueError("Conflicting publication dates")
    return Source(url=url, title=doc.title or url, text=doc.text[:14000], published_at=date,
                  updated_at=updated, date_basis="dated_advisory_heading" if updated else "publication_metadata",
                  retrieved_at=current, primary=primary(url, cfg), publisher=publisher(url))


def independent_sources(sources):
    accepted, hashes = [], set()
    for source in sources:
        hashed = hashlib.sha256(" ".join(source.text.lower().split()).encode()).hexdigest()
        if hashed in hashes or any(similarity(source.text, s.text) > .9 for s in accepted):
            continue
        hashes.add(hashed)
        accepted.append(source)
    if len({s.publisher for s in accepted}) < 2 or not any(s.primary for s in accepted):
        raise ValueError("Need two independent sources including an official primary source")
    return accepted


class ClaimSelections(BaseModel):
    claim_ids: list[str] = Field(min_length=2, max_length=2)


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
        if text.startswith(("-", "•")) or re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
            continue
        matches = []
        for other, quote in lookup.values():
            if other.publisher == source.publisher or other.primary:
                continue
            coverage = len(words(text) & words(quote))/max(1, len(words(text)))
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


def select_claims(llm, sources, folder=None, topic=None):
    candidates = corroborated_candidates(sources)
    if len(candidates) < 2:
        raise ValueError("Need two primary-source statements with independent corroboration")
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


def verify_claims(claims, sources):
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
            cw = words(claim.claim)
            if len(cw & words(quote)) / max(1, len(cw)) < .65:
                raise ValueError("Claim exceeds quoted evidence")
            for number in re.findall(r"\b\d+(?:[.,]\d+)*\b", claim.claim):
                if number not in quote:
                    raise ValueError("Numeric claim lacks independent support")
            families.add(source.publisher)
            has_primary |= source.primary
        if len(families) < 2 or not has_primary:
            raise ValueError("Each claim needs primary and independent corroboration")


def research(cfg, web, llm, topic, candidates, folder=None):
    urls = [(topic.url, topic.published_at), *[(u, None) for u in topic.related_urls]]
    urls.extend((t.url, t.published_at) for t in candidates
                if t.url != topic.url and similarity(topic.title, t.title) >= .35)
    sources, seen = [], set()
    index = 0
    while index < len(urls) and index < 16 and len(sources) < 6:
        url, hint = urls[index]
        index += 1
        if url in seen:
            continue
        seen.add(url)
        try:
            html = web.article(url)
            source = extract(url, html, cfg, hint)
            threshold = .25 if source.primary else .4
            if similarity(topic.title, source.title + " " + source.text) < threshold:
                continue
            sources.append(source)
            try:
                independent_sources(sources)
            except ValueError:
                pass
            else:
                break
            parser = Links()
            parser.feed(html)
            # Follow cited official announcements, never arbitrary links across the web.
            urls.extend((urljoin(url, link), None) for link in parser.links
                        if primary(urljoin(url, link), cfg) and urljoin(url, link) not in seen)
        except Exception:
            continue
    accepted = independent_sources(sources)
    sources, families = [], set()
    for source in sorted(accepted, key=lambda s: not s.primary):
        if source.publisher not in families:
            sources.append(source)
            families.add(source.publisher)
        if len(sources) == 3:
            break
    if folder:
        write_json(folder/"source_documents.json", [s.model_dump(mode="json") for s in sources])
    claims = select_claims(llm, sources, folder, topic.title)
    from .ai import Audit
    audit = llm.structured(Audit,
        "Independently audit these claims against retrieved documents. Reject contradictions, stale "
        "information, missing context, misleading comparison, unsupported certainty or numerical claims. "
        "Audit the supplied claims, not unrelated assertions elsewhere in the documents. For rolling "
        "vendor advisories, updated_at with date_basis=dated_advisory_heading identifies the current "
        "dated update; published_at may be the older index-page creation date. "
        "supported must be false if any material claim is not entailed by its quotations.",
        {"claims": [c.model_dump() for c in claims], "sources": [
            {**s.model_dump(exclude={"text"}), "current_context": s.text[:2000] if s.primary else s.text[:500]}
            for s in sources]})
    if not audit.supported:
        raise ValueError("Source evidence audit failed")
    return Research(sources=sources, claims=claims)
