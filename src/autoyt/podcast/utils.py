"""Utility helpers for podcast media processing."""
from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path


def get_ffmpeg_binary(cfg=None) -> str:
    """Return absolute path to ffmpeg binary."""
    if cfg and getattr(cfg, "ffmpeg", None):
        p = Path(cfg.ffmpeg)
        if p.is_file():
            return str(p.resolve())

    default_tool = Path("data/tools/ffmpeg.exe")
    if default_tool.is_file():
        return str(default_tool.resolve())

    which = shutil.which("ffmpeg")
    if which:
        return which

    return "ffmpeg"


def get_ffprobe_binary(cfg=None) -> str:
    """Return absolute path to ffprobe binary."""
    if cfg and getattr(cfg, "ffprobe", None):
        p = Path(cfg.ffprobe)
        if p.is_file():
            return str(p.resolve())

    default_tool = Path("data/tools/ffprobe.exe")
    if default_tool.is_file():
        return str(default_tool.resolve())

    which = shutil.which("ffprobe")
    if which:
        return which

    return "ffprobe"


# ---------------------------------------------------------------------------
# Urdu / Arabic & Devanagari -> English/Hinglish (Latin script) transliteration
# ---------------------------------------------------------------------------

URDU_TO_HINGLISH = {
    'آ': 'aa', 'ا': 'a', 'ب': 'b', 'پ': 'p', 'ت': 't', 'ٹ': 't', 'ث': 's',
    'ج': 'j', 'چ': 'ch', 'ح': 'h', 'خ': 'kh', 'د': 'd', 'ڈ': 'd', 'ذ': 'z',
    'ر': 'r', 'ڑ': 'r', 'ز': 'z', 'ژ': 'zh', 'س': 's', 'ش': 'sh', 'ص': 's',
    'ض': 'z', 'ط': 't', 'ظ': 'z', 'ع': 'a', 'غ': 'gh', 'ف': 'f', 'ق': 'q',
    'ک': 'k', 'گ': 'g', 'ل': 'l', 'م': 'm', 'ن': 'n', 'ں': 'n', 'و': 'o',
    'ہ': 'h', 'ھ': 'h', 'ء': '', 'ی': 'i', 'ے': 'e',
    'َ': 'a', 'ِ': 'i', 'ُ': 'u', 'ً': 'an', 'ّ': '', 'ْ': '',
}

DEVANAGARI_VOWELS = {
    'अ': 'a', 'आ': 'aa', 'इ': 'i', 'ई': 'ee', 'उ': 'u', 'ऊ': 'oo', 'ऋ': 'ri',
    'ए': 'e', 'ऐ': 'ai', 'ओ': 'o', 'औ': 'au', 'अं': 'am', 'अः': 'ah',
}

DEVANAGARI_MATRAS = {
    'ा': 'aa', 'ि': 'i', 'ी': 'ee', 'ु': 'u', 'ू': 'oo', 'ृ': 'ri',
    'े': 'e', 'ै': 'ai', 'ो': 'o', 'ौ': 'au', 'ं': 'n', 'ँ': 'n', 'ः': 'h',
}

DEVANAGARI_CONSONANTS = {
    'क': 'k', 'ख': 'kh', 'ग': 'g', 'घ': 'gh', 'ङ': 'ng',
    'च': 'ch', 'छ': 'chh', 'ज': 'j', 'झ': 'jh', 'ञ': 'ny',
    'ट': 't', 'ठ': 'th', 'ड': 'd', 'ढ': 'dh', 'ण': 'n',
    'त': 't', 'थ': 'th', 'द': 'd', 'ध': 'dh', 'न': 'n',
    'प': 'p', 'फ': 'ph', 'ब': 'b', 'भ': 'bh', 'म': 'm',
    'य': 'y', 'र': 'r', 'ल': 'l', 'व': 'v',
    'श': 'sh', 'ष': 'sh', 'स': 's', 'ह': 'h',
    'क़': 'q', 'ख़': 'kh', 'ग़': 'gh', 'ज़': 'z', 'ड़': 'r', 'ढ़': 'rh', 'फ़': 'f',
}


def transliterate_devanagari(text: str) -> str:
    """Convert Devanagari (Hindi) script into Romanized Hinglish."""
    res = []
    i = 0
    n = len(text)
    while i < n:
        if i + 1 < n and text[i:i+2] in DEVANAGARI_CONSONANTS:
            cons = DEVANAGARI_CONSONANTS[text[i:i+2]]
            i += 2
            if i < n and text[i] == '्':
                res.append(cons)
                i += 1
            elif i < n and text[i] in DEVANAGARI_MATRAS:
                res.append(cons + DEVANAGARI_MATRAS[text[i]])
                i += 1
            else:
                if i == n or text[i] in ' \t\n.,!?-:;()[]{}':
                    res.append(cons)
                else:
                    res.append(cons + 'a')
            continue

        char = text[i]
        if char in DEVANAGARI_CONSONANTS:
            cons = DEVANAGARI_CONSONANTS[char]
            i += 1
            if i < n and text[i] == '्':
                res.append(cons)
                i += 1
            elif i < n and text[i] in DEVANAGARI_MATRAS:
                res.append(cons + DEVANAGARI_MATRAS[text[i]])
                i += 1
            else:
                if i == n or text[i] in ' \t\n.,!?-:;()[]{}':
                    res.append(cons)
                else:
                    res.append(cons + 'a')
        elif char in DEVANAGARI_VOWELS:
            res.append(DEVANAGARI_VOWELS[char])
            i += 1
        elif char in DEVANAGARI_MATRAS:
            res.append(DEVANAGARI_MATRAS[char])
            i += 1
        elif char == '्':
            i += 1
        else:
            res.append(char)
            i += 1
    return ''.join(res)


def transliterate_urdu(text: str) -> str:
    """Convert Urdu / Arabic script into Romanized Hinglish."""
    res = []
    for ch in text:
        res.append(URDU_TO_HINGLISH.get(ch, ch))
    return ''.join(res)


def clean_to_english_hinglish(text: str, keep_emojis: bool = True) -> str:
    """Enforce strict English and Romanized Hinglish (Latin alphabet) representation.
    
    Transliterates any Urdu/Arabic script or Devanagari (Hindi) script into Romanized Hinglish,
    normalizes quotes and punctuation, and removes any non-Latin foreign scripts.
    """
    if not text:
        return ''
    
    # 1. Unicode NFC normalization
    text = unicodedata.normalize('NFC', text)
    
    # 2. Transliterate Urdu / Arabic characters to Hinglish
    text = transliterate_urdu(text)
    
    # 3. Transliterate Devanagari characters to Hinglish
    text = transliterate_devanagari(text)
    
    # 4. Filter characters: keep Latin alphabet, numbers, standard punctuation, and emojis
    cleaned = []
    for ch in text:
        code = ord(ch)
        # Standard ASCII and Latin extended
        if code < 0x0250:
            cleaned.append(ch)
        # Punctuation normalization
        elif ch in ('’', '‘'):
            cleaned.append("'")
        elif ch in ('“', '”'):
            cleaned.append('"')
        elif ch in ('–', '—'):
            cleaned.append('-')
        elif ch == '…':
            cleaned.append('...')
        elif ch in ('•', '·', '|', '/'):
            cleaned.append(ch)
        # Emojis (optional for titles/descriptions)
        elif keep_emojis and (
            0x1F300 <= code <= 0x1FAFF
            or 0x2600 <= code <= 0x27BF
            or 0x2300 <= code <= 0x23FF
            or 0x2B50 <= code <= 0x2B55
            or 0xFE00 <= code <= 0xFE0F
        ):
            cleaned.append(ch)
        elif ch.isspace():
            cleaned.append(' ')
        # Any other non-Latin foreign characters are discarded
    
    result = ''.join(cleaned)
    result = re.sub(r' +', ' ', result).strip()
    return result
