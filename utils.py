from __future__ import annotations
import pandas as pd

NBSP = "\u00A0"

def clean_text(x):
    if pd.isna(x):
        return x
    if not isinstance(x, str):
        return x
    s = x.replace(NBSP, " ").replace("\t"," ")
    s = s.replace("*","").replace("@","")
    s = " ".join(s.split())
    return s

ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ")

def norm(s: str | None) -> str | None:
    if pd.isna(s) or s is None:
        return None
    s = clean_text(str(s)).upper()
    kept = "".join(ch for ch in s if ch in ALLOWED)
    return " ".join(kept.split()) or None

def to_date(series, source: str = ""):
    formats = ["%d/%m/%Y", "%d %b %y", "%d %b %Y", "%d-%b-%y"]
    s = pd.Series([pd.NaT]*len(series), index=series.index)
    # Try each format in turn
    for fmt in formats:
        mask = s.isna() & series.notna()
        if mask.any():
            parsed = pd.to_datetime(series[mask], errors="coerce", format=fmt)
            s.loc[mask] = parsed
    # Fallback broad parser
    mask = s.isna() & series.notna()
    if mask.any():
        print(f"[to_date] Fallback parsing triggered in {source}. {mask.sum()} values could not parse with known formats. Examples: {series[mask].head(5).tolist()}")
        s.loc[mask] = pd.to_datetime(series[mask], errors="coerce", dayfirst=True)
    return s.dt.date

def to_number(series):
    return pd.to_numeric(series, errors="coerce")