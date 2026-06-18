# Bank Statements → Transactions (XLSX)

## Run
```bash
python main.py
```

## Default paths
- Base: `D:\OneDrive\Misc\Bank statements\`
- Data folders under `Data\`: `BARCLAYCARD`, `DRAYSONLT`, `MASTERCARD`, `REWARDBLK`
- Lookups: `lookups.xlsx` (sheets: `companies`, `categories`)
- Output: `Outputs\transactions_YYYYMMDD_HHMMSS.xlsx`

Override base via env:
```bash
set BASE_DIR=D:\OneDrive\Misc\Bank statements
python main.py
```