from __future__ import annotations
import shutil
import tempfile
from pathlib import Path

import pandas as pd

import db
from business_rules import apply_account_signs, exclude_transfers, normalize_companies, strip_description
from ingest import read_barclay_folder, read_natwest_folder, read_tesco_folder
from utils import clean_text

ACCOUNT_READERS: dict[str, tuple[str, str | None]] = {
    'Barclaycard 0%': ('barclay', None),
    'Current':        ('natwest', 'Current'),
    'NatWest 0%':     ('natwest', 'NatWest 0%'),
    'Credit Card':    ('natwest', 'Credit Card'),
    'Tesco 0%':       ('tesco',   None),
}


def process_uploads(files: list, account_type: str) -> tuple[pd.DataFrame, int]:
    """
    Save uploaded FileStorage objects to a temp dir, run the appropriate
    reader + business rules, apply DB lookups, and return (df, skipped_count).
    """
    tmpdir = Path(tempfile.mkdtemp())
    try:
        for f in files:
            safe_name = Path(f.filename).name
            f.save(str(tmpdir / safe_name))

        reader_type, account_name = ACCOUNT_READERS[account_type]

        if reader_type == 'barclay':
            df = read_barclay_folder(tmpdir)
        elif reader_type == 'natwest':
            df = read_natwest_folder(tmpdir, account_name)
        else:
            df = read_tesco_folder(tmpdir)

        initial_count = len(df)

        if df.empty:
            return df, 0

        for col in ('Description', 'Account Name'):
            if col in df.columns:
                df[col] = df[col].map(clean_text)

        df = exclude_transfers(df)
        df = strip_description(df)
        df = normalize_companies(df)

        tfc = df['Description'].str.contains(r'(?i)^TAX\s+FREE\s+CHILDCARE\b', na=False)
        df.loc[tfc, 'Description'] = 'Tax Free Childcare'

        df = apply_account_signs(df)

        # Apply mappings from DB
        conn = db.get_conn()
        comp_rows = conn.execute('SELECT description, company FROM company_map').fetchall()
        cat_rows  = conn.execute('SELECT company, category FROM category_map').fetchall()
        conn.close()

        comp_dict = {r['description'].upper().strip(): r['company'] for r in comp_rows}
        cat_dict  = {r['company'].upper().strip():     r['category'] for r in cat_rows}

        df['Company'] = df['Description'].apply(
            lambda d: comp_dict.get(str(d).upper().strip(), '__Unknown') if pd.notna(d) else '__Unknown'
        )
        df['Category'] = df['Company'].apply(
            lambda c: cat_dict.get(str(c).upper().strip(), '__Unknown')
        )

        # Normalise output columns
        df['Date']         = df['Date'].astype(str)
        df['Description']  = df['Description'].fillna('')
        df['Account Name'] = df['Account Name'].fillna(account_type)
        df['Source File']  = df['Source File'].fillna('')
        df['Value']        = df['Value'].where(pd.notna(df['Value']), None)

        skipped = initial_count - len(df)
        return df.reset_index(drop=True), max(skipped, 0)

    finally:
        shutil.rmtree(str(tmpdir), ignore_errors=True)
