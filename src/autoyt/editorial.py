"""Channel preferences and explicitly labelled, independently sourced explainers."""
from .discovery import AI_CODING, words
from .models import Topic


def category(title):
    tokens = words(title)
    if tokens & {"scam", "breach", "malware", "cybersecurity", "passkeys", "phishing"}:
        return "Cybersecurity & Digital Awareness"
    if tokens & AI_CODING:
        return "Artificial Intelligence & LLMs"
    if tokens & {"smartphone", "smartphones", "phone", "iphone", "pixel", "samsung", "gadget", "gadgets", "laptop"}:
        return "Smartphones & Gadgets"
    if tokens & {"robot", "robotics", "quantum", "semiconductor", "space", "ar", "vr"}:
        return "Future Technology & Innovation"
    if tokens & {"tips", "tutorial", "apps", "extension", "extensions", "git"}:
        return "Useful Apps, Websites & Tech Tips"
    return "Latest Technology News"


def evergreen_topics():
    # No invented publication dates: these are current-document explanations, not news.
    entries = [
        ("What are LLMs and how do they generate text?",
         "https://www.ibm.com/think/topics/large-language-models",
         "https://www.cloudflare.com/learning/ai/what-is-large-language-model/"),
        ("How retrieval augmented generation helps AI use your documents",
         "https://aws.amazon.com/what-is/retrieval-augmented-generation/",
         "https://www.ibm.com/think/topics/retrieval-augmented-generation"),
        ("Why prompt injection is a risk for AI assistants",
         "https://www.ibm.com/think/topics/prompt-injection",
         "https://www.cloudflare.com/learning/ai/prompt-injection/"),
        ("How passkeys protect sign-ins against phishing",
         "https://fidoalliance.org/passkeys/",
         "https://developers.google.com/identity/passkeys"),
        ("Why developers use Git version control",
         "https://git-scm.com/book/en/v2/Getting-Started-About-Version-Control",
         "https://www.atlassian.com/git/tutorials/what-is-version-control"),
    ]
    return [Topic(title=title, url=primary, related_urls=[other], published_at=None,
                  source="curated_documentation", content_kind="evergreen")
            for title, primary, other in entries]


def selection_details(topic, evidence, slot=None):
    content_category = category(topic.title)
    preferred = {"08:00": "AI, LLMs and major announcements",
                 "14:00": "smartphones, gadgets and software",
                 "20:00": "useful tools, security and future technology"}.get((slot or "")[-5:])
    return {"topic": topic.title, "category": content_category, "content_kind": topic.content_kind,
            "reason": ("Useful evergreen explanation selected after news candidates did not qualify."
                       if topic.content_kind == "evergreen" else
                       "Recent technology development with verified claims and practical audience value."),
            "slot_preference": preferred, "slot_rule": "Prefer the strongest verified story; categories are flexible.",
            "verified_summary": [c.claim for c in evidence.claims], "fact_check_status": "verified",
            "sources": [{"url": s.url, "published_at": s.published_at,
                         "retrieved_at": s.retrieved_at, "date_basis": s.date_basis} for s in evidence.sources]}


def content_package(topic, evidence, script, captions, assets, folder, slot=None):
    return {**selection_details(topic, evidence, slot), "script": script.script,
            "voiceover_text": script.script, "title": script.title, "description": script.description,
            "hashtags": script.hashtags, "scenes": [scene.model_dump() for scene in script.scenes],
            "timestamped_subtitles": captions, "subtitle_file": str((folder/"subtitles.srt").resolve()),
            "thumbnail_text": script.thumbnail_text,
            "thumbnail_concept": "Bold subject text over original dark circuit graphics with teal accents.",
            "thumbnail_file": str((folder/"thumbnail.png").resolve()), "visual_assets": assets}
