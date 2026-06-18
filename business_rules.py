from __future__ import annotations
import pandas as pd
import re
from utils import norm


def exclude_transfers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Exclude salary income, internal transfers, savings movements,
    investment platform flows, government/benefit items we don't track,
    and known credit-card payment refs.
    Run BEFORE any description normalization or text stripping.
    """
    # --- Normalise description once ---
    d = (
        df["Description"]
        .fillna("")
        .str.upper()
        .str.replace("\u00A0", " ", regex=False)   # NBSP → space
        .str.replace(r"\s+", " ", regex=True)      # collapse whitespace
        .str.strip()
    )
    acct = df["Account Name"].fillna("")
    # Type may be absent in final outputs; handle defensively
    typ = df.get("Type", pd.Series(index=df.index, dtype=object)).fillna("").str.upper()

    # --- Helper functions ---
    def startswith_any(s: pd.Series, prefixes: list[str]) -> pd.Series:
        if not prefixes:
            return pd.Series(False, index=s.index)
        mask = pd.Series(False, index=s.index)
        for p in prefixes:
            mask |= s.str.startswith(p, na=False)
        return mask

    def contains_any(s: pd.Series, needles: list[str]) -> pd.Series:
        if not needles:
            return pd.Series(False, index=s.index)
        mask = pd.Series(False, index=s.index)
        for n in needles:
            mask |= s.str.contains(n, na=False)
        return mask

    def contains_all(s: pd.Series, tokens: list[str]) -> pd.Series:
        if not tokens:
            return pd.Series(False, index=s.index)
        mask = pd.Series(True, index=s.index)
        for t in tokens:
            mask &= s.str.contains(t, na=False)
        return mask

    # --- Rule groups ---
    salary = ["MOTOR D LTD", "WAKEFIELD COLLEGE"]
    family_internal = [
        "ARTHUR DRAYSON", "ELOUISE BLAND", "LAUREN 150", "LAUREN DRAYSON",
        "THOMAS DRAYSON", "LAUREN ATOM", "TOM ATOM", "FIRST DIRECT",
        "LAUREN FIRST DIREC"
    ]
    transfer_prefixes = ["FROM A/C", "FP RETURN"]
    explicit_starts = ["TRADING 212", "TOM NATIONWIDE", "TO A/C 86396838", "MONEYBOX"]

    # Keep TAX FREE CHILDCARE (we track it), but still exclude HMRC CHILD BENEFIT
    benefits = ["HMRC CHILD BENEFIT"]

    rewards = ["INSTANT SAVER REWA"]
    barclaycard_payment = contains_all(d, ["BARCLAYCARD", "NORTHAMPTON"])

    # Bank transfer filters
    bac_contains = ["HEART OF YORKSHIRE", "TRADING 212", "HMRC PAYE", "A273E00A03C1NSPB"]
    bac_starts = ["L DRAYSON", "DRAYSON L"]

    # Investment/custody platforms to exclude entirely
    investment_providers = ["SECCL CUSTODY LTD"]

    # --- Compose mask ---
    exclude = (
        contains_any(d, salary) |
        contains_any(d, family_internal) |
        startswith_any(d, transfer_prefixes) |
        startswith_any(d, explicit_starts) |
        barclaycard_payment |
        contains_any(d, benefits) |
        startswith_any(d, rewards) |
        ((acct == "Current") & d.str.contains("NW MASTERCARD", na=False)) |
        ((typ == "BAC") & (contains_any(d, bac_contains) | startswith_any(d, bac_starts))) |
        contains_any(d, investment_providers)    # NEW: exclude SECCL CUSTODY LTD
    )

    return df.loc[~exclude].copy()


def strip_description(df: pd.DataFrame) -> pd.DataFrame:
    """
    Simplify Description strings by removing reference IDs or redundant trailing segments.
    Also enforces certain canonical forms for known payees.
    """
    def extract_description(desc):
        if pd.isna(desc):
            return desc

        # Hard rule: collapse anything starting with TAX FREE CHILDCARE
        if re.match(r'^\s*TAX\s+FREE\s+CHILDCARE\b', desc, flags=re.IGNORECASE):
            return "Tax Free Childcare"

        # Generic split on ' , ' segments
        parts = desc.split(" , ")
        if len(parts) < 2:
            return desc

        first = parts[0].strip()
        # Detect true numeric/date prefixes (e.g. '1881 05JUL25')
        if re.match(r'^\d{3,}\s+\d{2}[A-Z]{3}\d{2}', first):
            return " , ".join(parts[1:])
        else:
            # Otherwise keep up to the second part if it looks like a name segment (not FP or PYMT)
            if len(parts) >= 2 and not re.search(r'\bFP\b|\bPYMT\b|\bFPI\b', parts[1], re.IGNORECASE):
                return " , ".join(parts[:2])
            return first

    df["Description"] = df["Description"].apply(extract_description)
    return df


def normalize_companies(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize company names based on keywords in the description.
    """
    def normalize_description(desc):
        if pd.isna(desc):
            return desc

        # Enforce canonical 'Tax Free Childcare' anywhere it appears
        if re.search(r'\bTAX\s+FREE\s+CHILDCARE\b', desc, flags=re.IGNORECASE):
            return "Tax Free Childcare"

        # Remove leading www.
        desc = re.sub(r'^www\.', '', desc, flags=re.IGNORECASE)
        desc_upper = desc.upper()

        # Cash withdrawals
        if desc_upper.startswith(("GET CASH", "BMACH")) or "NOTEMACHINE" in desc_upper:
            return "Cash"

        # Common retailers / services
        if any(desc_upper.startswith(p) for p in ["AMZNMKTPLACE", "AMAZON"]) \
                or "AMAZON.CO.UK" in desc_upper \
                or "AMAZONCOUK" in desc_upper.replace(".", "").replace(" ", ""):
            return "Amazon"
        if desc_upper.startswith("EBAY"):
            return "eBay"
        if desc_upper.startswith("NETFLIX"):
            return "Netflix"
        if desc_upper.startswith("SPOTIFY"):
            return "Spotify"
        if "MARTIN FIRTH" in desc_upper:
            return "Martin Firth"
        if "TPP CHIP FINANCIAL" in desc_upper:
            return "Chip"
        if "ID MOBILE LIMITED" in desc_upper:
            return "ID Mobile"
        if "SUBWAY" in desc_upper:
            return "Subway"
        if "OCTOPUS ENERGY" in desc_upper:
            return "Octopus"
        if "JUST EAT UK" in desc_upper:
            return "Just Eat"
        if "KLEEN BINZ" in desc_upper:
            return "Kleen Binz"
        if "DVLA" in desc_upper:
            return "DVLA"
        if "CLAIRE CROW" in desc_upper:
            return "Claire Crow"

        return desc

    df["Description"] = df["Description"].apply(normalize_description)
    return df


def apply_account_signs(df: pd.DataFrame) -> pd.DataFrame:
    """
    Flip signs for current account entries so that expenditures are positive.
    Barclaycard/NatWest credit accounts are already correctly signed.
    """
    df.loc[df["Account Name"] == "Current", "Value"] *= -1
    return df