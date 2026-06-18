from __future__ import annotations
from pathlib import Path
import pandas as pd
from utils import clean_text, norm, to_date, to_number

def read_barclay_folder(path: Path) -> pd.DataFrame:
    rows = []
    for p in sorted(path.glob("*.csv")):
        try:
            df = pd.read_csv(p, header=None, dtype=str, encoding="utf-8")
        except UnicodeDecodeError:
            df = pd.read_csv(p, header=None, dtype=str, encoding="latin1")
        df = df.rename(columns={0:"Date",1:"Description",3:"Account Name",5:"Negatives",6:"Value"})
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].map(clean_text)
        df["Account Name"] = "Barclaycard 0%"
        df["Source File"] = p.name
        v = to_number(df.get("Value", pd.Series(index=df.index)))
        n = to_number(df.get("Negatives", pd.Series(index=df.index)))
        df["Value"] = v.where(v.notna(), n)
        if "Type" not in df.columns:
            df["Type"] = None
        rows.append(df[["Date","Type","Description","Account Name","Value","Source File"]])
    if not rows:
        return pd.DataFrame(columns=["Date","Type","Description","Account Name","Value","Source File"])
    out = pd.concat(rows, ignore_index=True)
    out["Date"] = to_date(out["Date"], source=str(path))
    out["Value"] = to_number(out["Value"])
    d = out["Description"].fillna("")
    mask = ~(
        d.str.contains("INTEREST ON YOUR PROMOTIONAL SPENDING", case=False, na=False) |
        d.str.contains("PAYMENT", case=False, na=False) |
        d.str.contains("THANK YOU", case=False, na=False)
    )
    return out.loc[mask].reset_index(drop=True)

def read_natwest_folder(path: Path, account_name_rule: str | None) -> pd.DataFrame:
    rows = []
    for p in sorted(path.glob("*.csv")):
        try:
            df = pd.read_csv(p, dtype=str, encoding="utf-8")
        except UnicodeDecodeError:
            df = pd.read_csv(p, dtype=str, encoding="latin1")
        rename_map = {
            "Transaction Date":"Date","DATE":"Date",
            "Type ":"Type","Transaction Type":"Type",
            "Details":"Description","Narrative":"Description","Desc":"Description","Transaction Details":"Description",
            "Amount":"Value","Debit":"Value","Credit":"Value",
            "Account":"Account Name","Acct":"Account Name"
        }
        df.rename(columns={k:v for k,v in rename_map.items() if k in df.columns}, inplace=True)
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].map(clean_text)
        if "Description" not in df.columns: df["Description"] = None
        if "Value" not in df.columns:
            for cand in ("Debit","Credit","Amount"):
                if cand in df.columns:
                    df["Value"] = df[cand]
                    break
        if account_name_rule:
            df["Account Name"] = account_name_rule
        elif "Account Name" not in df.columns:
            df["Account Name"] = None
        df["Source File"] = p.name
        desc = df["Description"].astype(str)
        desc = desc.str.replace(" , INITIAL PAYMENT","", regex=False)
        df["Description"] = desc.mask(desc.str.match(r"^\d{4}\s,\s"), desc.str.split(" , ", n=1).str[-1])
        if "Type" not in df.columns: df["Type"] = None
        df["Date"] = to_date(df.get("Date"), source=str(p))
        df["Value"] = to_number(df.get("Value"))
        rows.append(df[["Date","Type","Description","Account Name","Value","Source File"]])
    if not rows:
        return pd.DataFrame(columns=["Date","Type","Description","Account Name","Value","Source File"])
    out = pd.concat(rows, ignore_index=True)
    d = out["Description"].fillna("")
    t = out["Type"].fillna("")
    mask = ~(
        d.str.match(r"TESCO BANK$", case=False, na=False) |
        d.str.startswith("To A/C 36096393", na=False) |
        d.str.startswith("Balance as at", na=False) |
        d.str.startswith("DIRECT DEBIT PAYMENT - THANK YOU", na=False) |
        d.str.startswith("DIRECT DEBIT PAYMENT - TH", na=False) |
        d.str.startswith("FASTER PAYMENT RECEIVED -", na=False) |
        d.str.startswith("DIRECT DEBIT PAYMENT", na=False) |
        d.str.startswith("CASH FEE", na=False) |
        d.str.startswith("ANNUAL CARD FEE", na=False) |
        d.str.startswith("TRADING TWOONETWO", na=False) |
        d.str.startswith("NW REWARD BLACK", na=False) |
        d.str.contains("NS&I PREMIUM BONDS", na=False) |
        d.str.startswith("PLUM FINTECH", na=False) |
        d.str.startswith("ACC-NWESTPLAT", na=False) |
        d.str.startswith("B/CARD PLAT VISA", na=False) |
        (t.str.upper() == "CHG")
    )
    return out.loc[mask].reset_index(drop=True)

def read_tesco_folder(path: Path) -> pd.DataFrame:
    rows = []
    for p in sorted(path.glob("*.csv")):
        try:
            df = pd.read_csv(p, dtype=str, encoding="utf-8")
        except UnicodeDecodeError:
            df = pd.read_csv(p, dtype=str, encoding="latin1")
        
        # Rename columns to standard format
        df = df.rename(columns={
            "Transaction Date": "Date",
            "Merchant": "Description",
            "Billing Amount": "Value"
        })
        
        # Clean text columns
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].map(clean_text)
        
        df["Account Name"] = "Tesco 0%"
        df["Source File"] = p.name
        df["Type"] = None
        
        # Convert date
        df["Date"] = to_date(df["Date"], source=str(p))
        
        # Strip currency symbols (£ and �) before converting to number
        if "Value" in df.columns:
            df["Value"] = df["Value"].astype(str).str.replace("£", "", regex=False).str.replace("�", "", regex=False)
        
        # Convert value
        df["Value"] = to_number(df["Value"])
        
        rows.append(df[["Date", "Type", "Description", "Account Name", "Value", "Source File"]])
    
    if not rows:
        return pd.DataFrame(columns=["Date", "Type", "Description", "Account Name", "Value", "Source File"])
    
    out = pd.concat(rows, ignore_index=True)
    
    # Filter out interest charges and payment transactions
    d = out["Description"].fillna("")
    mask = ~(
        d.str.contains("INTEREST - SEE SUMMARY", case=False, na=False) |
        d.str.contains("DIRECT DEBIT PAYMENT", case=False, na=False)
    )
    
    return out.loc[mask].reset_index(drop=True)