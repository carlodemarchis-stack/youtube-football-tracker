"""Per-video language detection helper.

Combines three signals in priority order:
  1. YouTube's snippet.defaultAudioLanguage / defaultLanguage
     → most reliable but only ~30-50% of videos have it set
  2. Title-text detection via lingua-py
     → ~92% accurate on titles ≥ 30 chars; we restrict the candidate
     set to a handful of football-relevant European languages so
     short titles don't get misclassified into Tagalog/etc.
  3. Channel country prior
     → fallback when title is too short or detector confidence is low

Returns (language_iso2, source) where source ∈ {'youtube','detected','prior'}.
Language is lowercase ISO 639-1 (e.g. 'it', 'en', 'es', 'de', 'fr', 'pt').
"""
from __future__ import annotations

# Lazy-init the detector — lingua's first-call indexing is ~6MB / 1s,
# don't pay it on import.
_DETECTOR = None
_LANG_TO_ISO: dict = {}


# Channel.country → expected primary language ISO2.
# Used as the prior when title detection is ambiguous.
COUNTRY_TO_LANG = {
    "IT": "it",
    "ES": "es",
    "DE": "de",
    "FR": "fr",
    "GB": "en", "EN": "en", "UK": "en",
    "US": "en", "USA": "en",
    "PT": "pt",
    "BR": "pt",
    "NL": "nl",
    "BE": "fr",   # most football clubs in BE are FR-speaking
    "AR": "es",
    "MX": "es",
}


def _ensure_detector():
    """Build the detector once. Restricted to languages that actually appear
    in football channel content — keeps short-title accuracy high."""
    global _DETECTOR, _LANG_TO_ISO
    if _DETECTOR is not None:
        return
    try:
        from lingua import Language, LanguageDetectorBuilder
    except ImportError:
        return  # detection is optional; caller must check is_available()
    langs = [
        Language.ENGLISH, Language.ITALIAN, Language.SPANISH,
        Language.GERMAN, Language.FRENCH, Language.PORTUGUESE,
        Language.DUTCH, Language.CATALAN,
    ]
    _DETECTOR = LanguageDetectorBuilder.from_languages(*langs).build()
    _LANG_TO_ISO = {
        Language.ENGLISH:    "en",
        Language.ITALIAN:    "it",
        Language.SPANISH:    "es",
        Language.GERMAN:     "de",
        Language.FRENCH:     "fr",
        Language.PORTUGUESE: "pt",
        Language.DUTCH:      "nl",
        Language.CATALAN:    "ca",
    }


def is_available() -> bool:
    """True if lingua-py is installed and the detector built successfully."""
    _ensure_detector()
    return _DETECTOR is not None


def normalize(lang: str | None) -> str | None:
    """Coerce free-form language strings (en, en-US, en_GB, eng) to ISO 639-1."""
    if not lang:
        return None
    s = str(lang).strip().lower().replace("_", "-")
    if not s:
        return None
    head = s.split("-", 1)[0]
    # 3-letter ISO 639-2 → 1
    iso3_to_1 = {"eng": "en", "ita": "it", "spa": "es", "deu": "de", "ger": "de",
                 "fra": "fr", "fre": "fr", "por": "pt", "nld": "nl", "dut": "nl",
                 "cat": "ca"}
    if len(head) == 3:
        return iso3_to_1.get(head, head[:2])
    return head[:2] if len(head) >= 2 else None


def detect_language(title: str | None,
                    channel_country: str | None = None,
                    youtube_lang: str | None = None) -> tuple[str | None, str]:
    """Return (language_iso2, source).

    youtube_lang: pre-normalized value from snippet.defaultAudioLanguage or
    snippet.defaultLanguage. Wins outright if present.
    """
    yt = normalize(youtube_lang)
    if yt:
        return yt, "youtube"

    prior = COUNTRY_TO_LANG.get((channel_country or "").upper())

    # Try title-based detection. Branded English titles like
    #   "OFFICIAL HIGHLIGHTS | INTER VS MILAN | SERIE A 25/26"
    # would otherwise classify Italian content as English. So the bar to
    # OVERRIDE the country prior is set high — particularly for English
    # overrides, which are by far the most common false positive (sports
    # clubs love English-branded titles regardless of audio language).
    detected: str | None = None
    detected_conf = 0.0
    text = (title or "").strip()
    if text and is_available():
        import re
        cleaned = re.sub(r"[\#\|\:\!\?\.\,\-\—\–\(\)\[\]\{\}]", " ", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) >= 12:
            try:
                confs = _DETECTOR.compute_language_confidence_values(cleaned)
                if confs:
                    top = confs[0]
                    runner_val = confs[1].value if len(confs) > 1 else 0.0
                    margin = top.value - runner_val
                    # Require BOTH high confidence AND a clear margin —
                    # was previously OR (way too loose).
                    if top.value >= 0.70 and margin >= 0.15:
                        detected = _LANG_TO_ISO.get(top.language)
                        detected_conf = top.value
            except Exception:
                pass

    # Decision logic — prior is the strong default; detection only wins
    # when it agrees with the prior, OR when the override is decisive.
    if detected:
        if not prior or detected == prior:
            return detected, "detected"
        if detected == "en":
            # English-stuffed branded titles are the dominant false-positive
            # mode. Require very high confidence AND a long-ish title to
            # override a non-English country prior.
            if detected_conf >= 0.92 and len(text) >= 40:
                return "en", "detected"
            return prior, "prior"
        # Other cross-language overrides (e.g. ES content on an IT channel):
        # still need elevated confidence.
        if detected_conf >= 0.85:
            return detected, "detected"
        return prior, "prior"

    if prior:
        return prior, "prior"
    return None, "prior"
