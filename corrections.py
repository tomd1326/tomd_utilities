"""
Per-sender learned corrections for the audio transcriber.

Whisper mishears the same speaker in similar ways repeatedly — an accent,
an unusual name, a local place name. This module lets a user correct a
transcript once and have that correction considered again automatically
on future transcripts from the same sender, without retraining the model.

How it works:
  1. When a transcript is edited and saved, `diff_corrections` aligns the
     old (Whisper) text against the new (user-corrected) text and extracts
     the spans that changed, word-chunk by word-chunk.
  2. Each changed span is stored against the sender as a
     (wrong_text -> correct_text) rule, keyed by a phonetic encoding of
     wrong_text (Double Metaphone, per word).
  3. On a future transcript from that sender, `apply_corrections` looks at
     Whisper's own per-word confidence (via word_timestamps). Only runs of
     words Whisper itself was unsure about are checked against the
     sender's stored rules, using phonetic similarity rather than exact
     text match — this is what lets "thoroughbout" match a rule learned
     from "Arthur about" despite the spelling being unrelated.
  4. Matches above AUTO_APPLY_THRESHOLD are substituted automatically.
     Matches above SUGGEST_THRESHOLD but below that are left in the text
     and returned separately as suggestions, since applying a shaky guess
     automatically risks corrupting a word that was actually right.

Phonetic matching is inherently approximate. It is a good fit for
near-miss mishearings and run-together words; it will not recover
transcripts where Whisper invented content with no phonetic relationship
to what was said (e.g. hallucinated times/dates).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

import jellyfish
from metaphone import doublemetaphone

# Below this, a candidate correction is not shown at all — too dissimilar
# to be worth surfacing.
SUGGEST_THRESHOLD = 0.55

# At or above this, a correction is applied automatically.
AUTO_APPLY_THRESHOLD = 0.72

# A "low confidence" word is one Whisper itself was unsure about. Runs of
# these are what gets checked against stored rules — confident words are
# never touched, regardless of phonetic similarity to some other rule.
LOW_CONFIDENCE_THRESHOLD = 0.6


def _words(text: str) -> list[str]:
    return text.split()


def _phonetic(text: str) -> str:
    """Space-joined primary Double Metaphone code per word in text."""
    return ' '.join(doublemetaphone(w)[0] for w in _words(text) if w)


def phonetic_similarity(a: str, b: str) -> float:
    """0..1 similarity between two phrases based on how they sound."""
    pa, pb = _phonetic(a), _phonetic(b)
    if not pa or not pb:
        return 0.0
    return jellyfish.jaro_winkler_similarity(pa, pb)


@dataclass
class CorrectionSpan:
    wrong_text: str
    correct_text: str


def diff_corrections(original: str, corrected: str) -> list[CorrectionSpan]:
    """
    Word-level diff between Whisper's raw output and the user's edited
    version. Returns one CorrectionSpan per contiguous run that changed.

    Uses SequenceMatcher rather than a naive word-by-word zip because
    corrections routinely change the word count (e.g. one garbled word
    becoming a two-word phrase) — SequenceMatcher's opcodes handle that
    directly via 'replace'/'insert'/'delete' spans instead of requiring
    positions to line up.
    """
    old_words = _words(original)
    new_words = _words(corrected)
    sm = difflib.SequenceMatcher(a=old_words, b=new_words, autojunk=False)

    spans: list[CorrectionSpan] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        wrong = ' '.join(old_words[i1:i2]).strip()
        right = ' '.join(new_words[j1:j2]).strip()
        # A pure insertion has no "wrong" text to key a phonetic rule off,
        # so there's nothing to match against next time — skip it.
        if not wrong or not right:
            continue
        spans.append(CorrectionSpan(wrong_text=wrong, correct_text=right))
    return spans


def _low_confidence_runs(words: list[dict]) -> list[tuple[int, int]]:
    """
    Index ranges [start, end) of consecutive low-confidence words.
    `words` is a list of {'word': str, 'probability': float}.
    """
    runs = []
    start = None
    for i, w in enumerate(words):
        if w['probability'] < LOW_CONFIDENCE_THRESHOLD:
            if start is None:
                start = i
        else:
            if start is not None:
                runs.append((start, i))
                start = None
    if start is not None:
        runs.append((start, len(words)))
    return runs


@dataclass
class AppliedCorrection:
    original: str
    replacement: str
    similarity: float


@dataclass
class SuggestedCorrection:
    original: str
    suggested: str
    similarity: float


def apply_corrections(
    text: str,
    words: list[dict],
    rules: list[dict],
) -> tuple[str, list[AppliedCorrection], list[SuggestedCorrection]]:
    """
    Apply a sender's stored correction rules to a fresh transcript.

    `words` is faster-whisper's word_timestamps output, as a list of
    {'word': str, 'probability': float}, aligned with `text`.
    `rules` is the sender's stored corrections: [{'wrong_text', 'correct_text'}, ...].

    Only low-confidence runs of words are checked against rules — a
    confident word is never overridden, no matter how similar some rule
    sounds to it. Within a low-confidence run, every rule is scored by
    phonetic similarity against that run's text; the best match above
    SUGGEST_THRESHOLD is kept (auto-applied if it also clears
    AUTO_APPLY_THRESHOLD, otherwise returned as a suggestion).

    Returns (new_text, applied, suggestions). new_text has auto-applied
    corrections substituted in; suggestions are informational only.
    """
    if not rules or not words:
        return text, [], []

    runs = _low_confidence_runs(words)
    if not runs:
        return text, [], []

    applied: list[AppliedCorrection] = []
    suggestions: list[SuggestedCorrection] = []

    # Build replacements as (start_idx, end_idx, new_text) then apply them
    # back-to-front so earlier index ranges stay valid as we edit.
    replacements: list[tuple[int, int, str]] = []

    for start, end in runs:
        run_text = ' '.join(w['word'].strip() for w in words[start:end]).strip()
        if not run_text:
            continue

        best_rule = None
        best_score = 0.0
        for rule in rules:
            score = phonetic_similarity(run_text, rule['wrong_text'])
            if score > best_score:
                best_score = score
                best_rule = rule

        if best_rule is None or best_score < SUGGEST_THRESHOLD:
            continue

        if best_score >= AUTO_APPLY_THRESHOLD:
            replacements.append((start, end, best_rule['correct_text']))
            applied.append(AppliedCorrection(
                original=run_text,
                replacement=best_rule['correct_text'],
                similarity=best_score,
            ))
        else:
            suggestions.append(SuggestedCorrection(
                original=run_text,
                suggested=best_rule['correct_text'],
                similarity=best_score,
            ))

    if not replacements:
        return text, applied, suggestions

    # Rebuild text from the word list with replacements spliced in, since
    # `text` may have punctuation/casing that doesn't line up 1:1 with
    # `words` after edits. Working from `words` keeps indices consistent.
    out: list[str] = []
    i = 0
    replacements.sort(key=lambda r: r[0])
    rmap = {start: (end, new) for start, end, new in replacements}
    while i < len(words):
        if i in rmap:
            end, new = rmap[i]
            out.append(new)
            i = end
        else:
            out.append(words[i]['word'].strip())
            i += 1
    return ' '.join(out), applied, suggestions
