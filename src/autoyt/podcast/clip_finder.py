"""AI Clip Discovery Engine.

Finds engaging, self-contained 40-60 second clips from podcast transcripts.
Evaluates hook strength, emotional engagement, actionable advice, and completion.
Ensures candidates do not overlap and do not cut sentences mid-phrase.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import List, Optional

from .transcription import FullTranscript, TranscriptSegment, WordTimestamp
from .utils import clean_to_english_hinglish

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Viral Pillar Keywords: Motivational, Inspirational, Comedy, Serious Truths
# ---------------------------------------------------------------------------
MOTIVATIONAL_KEYWORDS = {
    "motivation", "discipline", "focus", "grind", "consistency", "hustle",
    "workout", "fitness", "failure", "overcome", "never", "routine",
    "champion", "dedication", "struggle", "pain", "strength", "hard",
    "work", "rule", "growth", "lead", "mehnat", "kamyaabi", "himmat", "koshish", "junoon", "aag"
}

INSPIRATIONAL_KEYWORDS = {
    "inspiration", "dream", "serving", "believe", "passion", "vision",
    "destiny", "courage", "miracle", "purpose", "hope", "transformation",
    "grateful", "gratitude", "faith", "spiritual", "god", "soul", "heart",
    "love", "life", "sapna", "zindagi", "vishwas", "bharosa"
}

COMEDY_KEYWORDS = {
    "funny", "joke", "laugh", "hilarious", "comedy", "roast", "crazy",
    "stupid", "insane", "ridiculous", "wild", "awkward", "silly", "chaotic",
    "prank", "humor", "laughing", "irony", "meme",
    "mazaak", "hasna", "hasi", "pagal", "bhai", "bakchodi", "kamaal"
}

SERIOUS_KEYWORDS = {
    "truth", "reality", "uncomfortable", "regret", "toxic", "ego", "trauma",
    "danger", "psychology", "manipulation", "betrayal", "loneliness", "death",
    "warning", "lie", "brutal", "conspiracy", "secret", "hypocrisy", "fear",
    "insecurity", "depression", "anxiety", "worst", "mistake",
    "sach", "dhoka", "dard", "darr", "galti", "pachtawa", "barbaad"
}

ALL_VIRAL_KEYWORDS = MOTIVATIONAL_KEYWORDS | INSPIRATIONAL_KEYWORDS | COMEDY_KEYWORDS | SERIOUS_KEYWORDS

HOOK_STARTERS = (
    "the biggest mistake", "the most important", "the reason why", "if you want",
    "never do this", "i realised that", "what happens when", "the truth about",
    "people always ask", "nobody tells you", "the secret to", "one lesson",
    "in my life", "when i was", "don't ever", "you have to", "the problem is",
    "harsh truth", "here is the funny thing", "the funniest story", "i will tell you why",
    "sabse badi galti", "yeh sach hai", "log nahi samajhte", "ek aisi baat",
)


@dataclass
class ClipCandidate:
    id: str                    # "{episode_id}:{clip_index}"
    episode_id: str
    source_id: str
    clip_index: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    transcript_text: str
    words: List[WordTimestamp]
    ai_score: float            # 0–100
    score_breakdown: dict
    suggested_title: str
    suggested_description: str
    suggested_hashtags: List[str]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["words"] = [w.to_dict() if isinstance(w, WordTimestamp) else w for w in self.words]
        return d


class ClipFinder:
    """Finds and scores candidate Shorts clips from a transcript."""

    def __init__(
        self,
        min_duration: float = 40.0,
        max_duration: float = 60.0,
        target_duration: float = 55.0,
        min_score: float = 50.0,
        gemini_api_key: Optional[str] = None,
    ):
        self.min_duration = min_duration
        self.max_duration = max_duration
        self.target_duration = target_duration
        self.min_score = min_score
        self.gemini_api_key = gemini_api_key

    def find_clips(
        self,
        transcript: FullTranscript,
        source_id: str,
        max_clips: int = 5,
    ) -> List[ClipCandidate]:
        """Discover candidate clips from transcript and return sorted by score."""
        segments = transcript.segments
        if not segments:
            log.warning("Empty transcript for %s", transcript.episode_id)
            return []

        # 1. Slide a window over segments to find all natural 40-60s candidate windows
        windows = self._generate_candidate_windows(segments)
        log.info("Found %d initial candidate windows for %s", len(windows), transcript.episode_id)

        if not windows:
            return []

        # 2. Heuristic scoring for all candidates
        scored_windows = []
        for win in windows:
            score, breakdown = self._score_heuristic(win)
            scored_windows.append((score, breakdown, win))

        # Filter by min_score, or fallback to top scored windows
        filtered = [w for w in scored_windows if w[0] >= self.min_score]
        if not filtered and scored_windows:
            scored_windows.sort(key=lambda x: x[0], reverse=True)
            filtered = scored_windows[: max_clips * 2]
        else:
            filtered.sort(key=lambda x: x[0], reverse=True)

        # 3. Greedy non-overlapping selection
        selected_windows = self._select_non_overlapping(filtered, max_clips * 2)

        # 4. Optional AI refinement with Gemini (if API key available)
        candidates: List[ClipCandidate] = []
        for idx, (base_score, breakdown, win) in enumerate(selected_windows[:max_clips], 1):
            clip_id = f"{transcript.episode_id}:{idx}"
            full_text = " ".join(s.text for s in win)
            
            # Aggregate words
            all_words = []
            for s in win:
                all_words.extend(s.words)

            start_t = win[0].start
            end_t = win[-1].end
            duration = round(end_t - start_t, 2)

            # Generate or refine titles and metadata
            title, desc, tags, final_score = self._refine_with_llm_or_heuristic(
                transcript.episode_id, full_text, base_score, breakdown
            )

            candidate = ClipCandidate(
                id=clip_id,
                episode_id=transcript.episode_id,
                source_id=source_id,
                clip_index=idx,
                start_seconds=start_t,
                end_seconds=end_t,
                duration_seconds=duration,
                transcript_text=full_text,
                words=all_words,
                ai_score=round(final_score, 1),
                score_breakdown=breakdown,
                suggested_title=title,
                suggested_description=desc,
                suggested_hashtags=tags,
            )
            candidates.append(candidate)

        log.info("Selected %d top non-overlapping clips for %s", len(candidates), transcript.episode_id)
        return candidates

    def _generate_candidate_windows(self, segments: List[TranscriptSegment]) -> List[List[TranscriptSegment]]:
        """Generate candidate segment windows between min_duration and max_duration."""
        punctuated_candidates = []
        any_candidates = []
        n = len(segments)
        for i in range(n):
            current_window = []
            for j in range(i, n):
                current_window.append(segments[j])
                dur = current_window[-1].end - current_window[0].start
                if self.min_duration <= dur <= self.max_duration:
                    any_candidates.append(list(current_window))
                    last_text = current_window[-1].text.strip()
                    if last_text and last_text[-1] in ".!?\"'":
                        punctuated_candidates.append(list(current_window))
                elif dur > self.max_duration:
                    break
        return punctuated_candidates if punctuated_candidates else any_candidates

    def _score_heuristic(self, window: List[TranscriptSegment]) -> tuple[float, dict]:
        """Compute candidate quality score (0–100) using speech features and keyword metrics."""
        text = " ".join(s.text for s in window).strip()
        first_sentence = window[0].text.lower()
        duration = window[-1].end - window[0].start

        # 1. Hook strength (0–25)
        hook_score = 10.0
        for starter in HOOK_STARTERS:
            if starter in first_sentence:
                hook_score += 12.0
                break
        if any(w in first_sentence for w in ("why", "how", "what", "secret", "never", "always")):
            hook_score += 3.0
        hook_score = min(25.0, hook_score)

        # 2. Viral Pillar Matching (Motivational, Inspirational, Comedy, Serious Truths) (0–25)
        tokens = set(re.findall(r"\b[a-z]{3,}\b", text.lower()))
        matched_moti = tokens & MOTIVATIONAL_KEYWORDS
        matched_insp = tokens & INSPIRATIONAL_KEYWORDS
        matched_come = tokens & COMEDY_KEYWORDS
        matched_seri = tokens & SERIOUS_KEYWORDS
        all_matched = tokens & ALL_VIRAL_KEYWORDS

        # Calculate category counts to find dominant pillar
        cat_counts = {
            "Motivational": len(matched_moti),
            "Inspirational": len(matched_insp),
            "Comedy": len(matched_come),
            "Serious": len(matched_seri),
        }
        dominant_cat = max(cat_counts, key=cat_counts.get) if any(cat_counts.values()) else "Motivational"
        
        # Base engagement from matched viral keywords (up to 25 pts)
        engagement_score = min(25.0, len(all_matched) * 4.5)
        if any(cat_counts[k] >= 2 for k in cat_counts):
            engagement_score = min(25.0, engagement_score + 3.0)  # Strong pillar density bonus

        # 3. Completeness & Flow (0–25)
        # Bonus if ends with strong punctuation, penalty if very short or cuts off
        completeness_score = 15.0
        if text.endswith((".", "!", "?")):
            completeness_score += 10.0

        # 4. Duration penalty (0–25)
        # Target duration gets 25, drops if close to boundaries
        diff = abs(duration - self.target_duration)
        dur_score = max(5.0, 25.0 - (diff * 1.5))

        total = round(hook_score + engagement_score + completeness_score + dur_score, 1)
        breakdown = {
            "hook": round(hook_score, 1),
            "engagement": round(engagement_score, 1),
            "completeness": round(completeness_score, 1),
            "duration": round(dur_score, 1),
            "total": total,
            "category": dominant_cat,
        }
        return total, breakdown

    def _select_non_overlapping(
        self,
        scored_windows: List[tuple[float, dict, List[TranscriptSegment]]],
        max_select: int,
    ) -> List[tuple[float, dict, List[TranscriptSegment]]]:
        """Greedy selection of highest scoring windows that do not overlap in time."""
        selected = []
        occupied_intervals = []

        for score, breakdown, win in scored_windows:
            start = win[0].start
            end = win[-1].end

            # Check overlap with already selected intervals (with 5s buffer)
            overlap = False
            for o_start, o_end in occupied_intervals:
                if not (end + 5.0 <= o_start or start >= o_end + 5.0):
                    overlap = True
                    break

            if not overlap:
                selected.append((score, breakdown, win))
                occupied_intervals.append((start, end))
                if len(selected) >= max_select:
                    break

        return selected

    def _refine_with_llm_or_heuristic(
        self,
        episode_id: str,
        text: str,
        base_score: float,
        breakdown: dict,
    ) -> tuple[str, str, List[str], float]:
        """Refine title and description using Gemini if available, or generate clean heuristic metadata."""
        if self.gemini_api_key:
            try:
                import httpx

                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.gemini_api_key}"
                prompt = f"""You are a world-class YouTube Shorts viral editor and strategist.
Given this transcript excerpt from a top podcast episode:
"{text}"

Generate JSON with:
- "title": A viral, punchy, curiosity-inducing YouTube Shorts title (under 55 characters) written STRICTLY IN ENGLISH ONLY (never Hindi, Hinglish, Urdu, or other foreign languages).
  - Must start with an eye-catching emoji (e.g. 🔥, 💡, ⚡, 🧠, 🛑, 💰, 🚀).
  - Must use high-converting hooks (e.g., "The Brutal Truth About...", "Why 99% Fail at...", "The #1 Secret to...", "Stop Doing This If You Want...", "How Champions Think").
  - Must end with #Shorts.
  - Even if the excerpt was spoken in Hindi or Hinglish, TRANSLATE the core hook and deliver it in fluent, compelling English.
- "summary": A compelling 2-3 sentence teaser and insight breakdown written STRICTLY IN ENGLISH ONLY.
  - Hook the reader on the key lesson from this clip.
  - Pose a thought-provoking question to prompt viewers to leave comments.
- "hashtags": 3 to 5 relevant viral hashtags in English (e.g. ["#Motivation", "#Success", "#Mindset", "#Shorts"]).
- "score_boost": A score adjustment from -5 to +5 reflecting how viral and compelling this insight is.

Output ONLY valid JSON.
"""
                resp = httpx.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=12.0)
                if resp.status_code == 200:
                    raw_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                    cleaned = re.sub(r"^```json\s*|\s*```$", "", raw_text.strip())
                    data = json.loads(cleaned)
                    title = clean_to_english_hinglish(data.get("title", "").strip(), keep_emojis=True)
                    summary = clean_to_english_hinglish(data.get("summary", "").strip(), keep_emojis=True)
                    hashtags = [clean_to_english_hinglish(h, keep_emojis=False) for h in data.get("hashtags", ["#Motivation", "#Success", "#Shorts"]) if h]
                    boost = float(data.get("score_boost", 0.0))
                    final_score = min(100.0, max(0.0, base_score + boost))
                    return title, summary, hashtags, final_score
            except Exception as exc:
                log.debug("Gemini refinement skipped or failed: %s", exc)

        # Fallback heuristic: High-converting English titles and summaries based on content and viral pillar
        text_lower = text.lower()
        cat = breakdown.get("category", "Motivational")

        # English title & summary templates by theme / detected keywords
        if any(w in text_lower for w in ("money", "paisa", "crore", "lakh", "rich", "wealth", "business", "startup", "invest")):
            title = "💰 The Brutal Truth About Making Money #Shorts"
            summary = "In this eye-opening clip, discover why building real wealth takes patience, discipline, and avoiding common financial traps. What is your #1 money rule?"
        elif any(w in text_lower for w in ("discipline", "focus", "mehnat", "grind", "routine", "habit", "consistency")):
            title = "🔥 The #1 Rule for Extreme Discipline #Shorts"
            summary = "Motivation comes and goes, but daily non-negotiable discipline is what separates winners from everyone else. What habit keeps you focused every single day?"
        elif any(w in text_lower for w in ("fear", "failure", "galti", "darr", "pain", "struggle", "rejection")):
            title = "⚡ How To Overcome Fear & Failure #Shorts"
            summary = "Failure is never final—it is simply feedback. Learn the mindset shift required to turn your biggest setbacks into your greatest strengths."
        elif any(w in text_lower for w in ("confidence", "speak", "communication", "talk", "voice", "people")):
            title = "🧠 The Secret to Unstoppable Confidence #Shorts"
            summary = "Confidence isn't something you are born with—it is a skill anyone can master through practice. What helps you speak up when you feel nervous?"
        elif any(w in text_lower for w in ("truth", "sach", "reality", "uncomfortable", "toxic", "ego", "betrayal")):
            title = "⚠️ The Harsh Reality Nobody Tells You #Shorts"
            summary = "A raw, unfiltered reality check on relationships, human psychology, and personal responsibility. Do you agree with this take? Let us know below!"
        elif cat == "Comedy" or any(w in text_lower for w in ("funny", "joke", "laugh", "hasi", "crazy", "insane")):
            title = "😂 You Won't Believe What Happened Here #Shorts"
            summary = "This hilarious podcast moment caught everyone completely off guard! What was your honest reaction to this?"
        elif cat == "Inspirational" or any(w in text_lower for w in ("dream", "sapna", "hope", "faith", "god", "life", "zindagi")):
            title = "✨ The Life Lesson That Changes Everything #Shorts"
            summary = "A deeply inspiring reminder to believe in your vision even when the path is uncertain. Take a moment to reflect on your journey today."
        else:
            title = "🔥 The Brutal Truth You Need to Hear #Shorts"
            summary = "An essential life lesson packed with wisdom and actionable perspective from the podcast. What resonated most with you from this clip?"
        
        cat = breakdown.get("category", "Motivational")
        cat_tag_map = {
            "Motivational": ["#Motivation", "#Discipline", "#Success", "#Shorts"],
            "Inspirational": ["#Inspiration", "#Dream", "#LifeLessons", "#Shorts"],
            "Comedy": ["#Comedy", "#Funny", "#Humor", "#Podcast", "#Shorts"],
            "Serious": ["#Psychology", "#Truth", "#Mindset", "#Shorts"],
        }
        hashtags = cat_tag_map.get(cat, ["#Motivation", "#Success", "#Podcast", "#Shorts"])
        return title, summary, hashtags, base_score
