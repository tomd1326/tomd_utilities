"""
Audio transcription via faster-whisper (local, offline).

Handles OGG/Opus WhatsApp voice notes plus the usual audio containers.
Decoding goes through PyAV, which is bundled with faster-whisper, so no
system ffmpeg install is required.
"""
from __future__ import annotations

import threading
from pathlib import Path

from faster_whisper import WhisperModel

# Whisper model sizes, smallest first. 'base' is a reasonable default for
# voice notes: noticeably better than 'tiny' on accents and background noise
# while still running comfortably on CPU.
MODEL_SIZES = [
    ('tiny',   'Tiny — fastest, roughly 75 MB'),
    ('base',   'Base — good balance (default)'),
    ('small',  'Small — more accurate, slower'),
    ('medium', 'Medium — high accuracy, much slower'),
]

DEFAULT_MODEL = 'base'

# Extensions we accept. WhatsApp exports voice notes as .ogg (Opus).
ALLOWED_AUDIO = {
    '.ogg', '.oga', '.opus',
    '.mp3', '.m4a', '.aac', '.wav', '.flac', '.wma',
    '.mp4', '.mkv', '.webm',
}

# Models are expensive to construct, so keep one per size for the process
# lifetime. Guarded because Flask serves requests on multiple threads.
_model_cache: dict[str, WhisperModel] = {}
_model_lock = threading.Lock()


def is_allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_AUDIO


def get_model(size: str = DEFAULT_MODEL) -> WhisperModel:
    """Return a cached WhisperModel, downloading weights on first use."""
    if size not in _model_cache:
        with _model_lock:
            # Re-check inside the lock: another thread may have won the race.
            if size not in _model_cache:
                _model_cache[size] = WhisperModel(
                    size, device='cpu', compute_type='int8'
                )
    return _model_cache[size]


def format_timestamp(seconds: float) -> str:
    """Seconds -> M:SS (or H:MM:SS past an hour)."""
    total = int(seconds)
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    if h:
        return f'{h}:{m:02d}:{s:02d}'
    return f'{m}:{s:02d}'


def transcribe_file(
    path: str | Path,
    model_size: str = DEFAULT_MODEL,
    language: str | None = None,
    progress_cb=None,
) -> dict:
    """
    Transcribe an audio file.

    `language` as None lets Whisper auto-detect; pass e.g. 'en' to skip
    detection and avoid the occasional wrong guess on short clips.

    Returns a dict with the full text, per-segment timings, detected
    language and audio duration.
    """
    model = get_model(model_size)

    segments, info = model.transcribe(
        str(path),
        language=language,
        beam_size=5,
        # Drops silence before it reaches the model — a solid speed win on
        # voice notes, which tend to open and close with dead air.
        vad_filter=True,
        vad_parameters={'min_silence_duration_ms': 500},
        # Needed so sender corrections can target only the words Whisper
        # itself was unsure about (see corrections.py).
        word_timestamps=True,
    )

    # `segments` is a generator; consuming it is what actually runs inference.
    # Each segment carries an end timestamp, so comparing that against the
    # total duration gives genuine progress rather than a fake animation.
    total = info.duration or 0
    seg_list = []
    parts = []
    word_list = []
    for seg in segments:
        if progress_cb and total:
            progress_cb(min(seg.end / total, 1.0))
        text = seg.text.strip()
        if not text:
            continue
        seg_list.append({
            'start':      seg.start,
            'end':        seg.end,
            'start_label': format_timestamp(seg.start),
            'end_label':   format_timestamp(seg.end),
            'text':       text,
        })
        parts.append(text)
        for w in (seg.words or []):
            word_list.append({
                'word':        w.word.strip(),
                'start':       w.start,
                'end':         w.end,
                'probability': w.probability,
            })

    return {
        'text':         ' '.join(parts),
        'segments':     seg_list,
        'words':        word_list,
        'language':     info.language,
        'language_prob': info.language_probability,
        'duration':     info.duration,
        'duration_label': format_timestamp(info.duration),
        'model':        model_size,
    }
