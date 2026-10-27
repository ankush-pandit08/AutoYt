"""Small exact-source context windows for CPU inference; never synthesize evidence."""
import re

from .discovery import similarity


def output_format(schema):
    value = schema.model_json_schema()
    if schema.__name__ == "NaturalEditorialDraft":
        props = value["properties"]
        for name, maximum in (("headlines", 50), ("visual_queries", 80), ("narration", 500)):
            props[name]["minItems"] = props[name]["maxItems"] = 3
            props[name]["items"]["maxLength"] = maximum
        props["description"]["maxLength"] = 240
    return value


def compact_context(evidence, total=3000):
    contexts = []
    for source in evidence.sources:
        quotes = [e.quote for c in evidence.claims for e in c.evidence if e.source_url == source.url]
        spans = []
        for quote in quotes:
            match = re.search(r"\s+".join(re.escape(part) for part in quote.split()), source.text, re.I)
            if match:
                spans.append((max(0, match.start() - 180), min(len(source.text), match.end() + 180)))
        if not spans:
            # Exact quote membership is checked separately before use.
            spans = [(0, min(600, len(source.text)))]
        merged = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        text = "\n[…]\n".join(source.text[a:b] for a, b in merged)
        available = max(0, total - sum(len(c["text"]) for c in contexts))
        if not available:
            break
        contexts.append({"url": source.url, "text": text[:available], "excerpted": True})
    return contexts


def ranked_passages(passages, topic, count=6, size=2200):
    selected = {}
    for key, value in sorted(passages.items(), key=lambda item: similarity(topic, item[1][1]), reverse=True):
        if len(selected) >= count or len(value[1]) + sum(len(v[1]) for v in selected.values()) > size:
            continue
        selected[key] = value
    return selected
