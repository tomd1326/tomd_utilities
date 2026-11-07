"""
Starting rows for the Plan & Platforms page.

Loaded into the database once, the first time the app runs; after that they
are edited in the page. Fees and tax figures were checked on the date in
each row. "Unconfirmed" means it could not be read on the provider's own
page that day.
"""
from __future__ import annotations

CHECKED = '2026-11-06'

# (name, holding_fee, dealing_fee, fx_fee, accounts, yearly_cost, notes, source_url, checked_on)
# yearly_cost: £50,000 held in ETFs in one account with 12 trades a year.
PLATFORMS = [
    ('IG (Share Dealing)',
     '£0',
     '£0 on UK and US shares',
     '0.7%',
     'ISA, General Account, SIPP (£210 a year to the administrator)',
     0.0,
     'Pays 4% on uninvested cash up to £100k if you held or traded in the last 30 days. '
     'Its spread betting and CFD side is a separate borrowed-money product: IG publishes '
     'that 70% of retail accounts lose money on it. Keep to the share dealing account.',
     'https://www.ig.com/uk/investments/share-dealing/costs-fees', CHECKED),
    ('Trading 212',
     '£0',
     '£0',
     '0.15%',
     'ISA, General Account, SIPP',
     0.0,
     'Pays 3.8% on ISA cash. Shares and ETFs only, no investment trusts or funds (unconfirmed). '
     'Card deposits over £2,000 in total cost 0.7% (unconfirmed); bank transfer is free. '
     'Figures read from its help centre, as the main site could not be opened.',
     'https://helpcentre.trading212.com/hc/en-us/articles/11471996799517-What-are-the-fees-in-the-Invest-ISAs-and-SIPP', CHECKED),
    ('InvestEngine',
     '£0 (0.25% if they manage it for you)',
     '£0',
     'None: pound-priced ETFs only (unconfirmed)',
     'ISA, General Account, SIPP',
     0.0,
     'ETFs only. No individual shares or investment trusts.',
     'https://investengine.com/', CHECKED),
    ('Freetrade',
     'Basic £0; Standard £4.99 a month; Plus £9.99 a month',
     '£0',
     '0.99% Basic; 0.59% Standard; 0.39% Plus',
     'ISA, General Account, SIPP (all plans)',
     0.0,
     'Yearly cost shown is the Basic plan. Cash interest is low: 1% on up to £1,000 on Basic. '
     'Owned by IG Group (unconfirmed).',
     'https://freetrade.io/pricing', CHECKED),
    ('Vanguard Investor',
     '0.15%, at most £375 a year; £4 a month minimum under £32,000',
     'Free when dealt in bulk at set times (unconfirmed); £7.50 for an immediate ETF trade',
     'Not applicable',
     'ISA, General Account, SIPP (unconfirmed)',
     75.0,
     'Vanguard funds and ETFs only, no individual shares (unconfirmed).',
     'https://www.vanguardinvestor.co.uk/what-we-offer/fees-explained', CHECKED),
    ('AJ Bell',
     '0.25% on shares, ETFs and trusts, at most £3.50 a month (£42 a year)',
     '£5 (£3.50 after 10 deals the month before); monthly investing free',
     'At most 0.75% (bands unconfirmed)',
     'ISA, General Account, SIPP',
     102.0,
     'Wide range of shares, ETFs, trusts and funds. £42 a year if you only use free monthly '
     'investing. The £42 limit is thought to apply to each account separately (unconfirmed).',
     'https://www.ajbell.co.uk/isa/stocks-shares-isa/charges', CHECKED),
    ('Interactive Investor',
     'Flat £5.99 a month up to £100k, covering ISA, SIPP and General Account',
     '£3.99 on UK and US shares; monthly investing free',
     '0.75%',
     'ISA, General Account, SIPP',
     119.76,
     'One flat fee covers every account, so it does not rise when money is split between an '
     'ISA and a general account. Wide range. Owned by Aberdeen (unconfirmed).',
     'https://www.ii.co.uk/our-charges', CHECKED),
    ('Hargreaves Lansdown',
     '0.35% on shares, ETFs and trusts, at most £12.50 a month (£150 a year) for each account',
     '£6.95 (£3.95 after 20 deals the month before); monthly investing free',
     'Unconfirmed',
     'ISA, General Account, SIPP',
     233.40,
     'Widest range and the best-known name, but the dearest here. Pays 1.30% on ISA cash under '
     '£20k. Charges page read through search results only, not opened directly.',
     'https://www.hl.co.uk/investment-services/isa/savings-interest-rates-and-charges', CHECKED),
]

# (topic, fact, source_url, checked_on)
FACTS = [
    ('ISA Allowance 2026/27',
     '£20,000 a tax year across all your ISAs. Growth and income inside it are free of tax. '
     'The year ends on 5 April 2027; unused allowance is lost.',
     'https://www.gov.uk/individual-savings-accounts', CHECKED),
    ('ISA Changes From 6 April 2027',
     'Under 65s can put at most £12,000 of the £20,000 into a cash ISA. Money cannot be moved '
     'from a stocks and shares ISA to a cash ISA, and interest on cash left inside a stocks '
     'and shares ISA will carry a charge. The £20,000 total is unchanged.',
     'https://www.gov.uk/government/publications/reduction-in-the-cash-individual-savings-account-isa-limit/cash-individual-savings-account-isa-limit-reduction', CHECKED),
    ('Capital Gains Tax 2026/27',
     'Outside an ISA, the first £3,000 of gains a year is free. Above that, 18% within the '
     'basic-rate band and 24% above it.',
     'https://www.gov.uk/capital-gains-tax/rates', CHECKED),
    ('Dividend Tax 2026/27',
     'Outside an ISA, the first £500 of dividends a year is free. Above that, 10.75% basic '
     'rate, 35.75% higher rate, 39.35% additional rate.',
     'https://www.gov.uk/tax-on-dividends', CHECKED),
    ('Protection If A Firm Fails (FSCS)',
     'Bank deposits: £120,000 for each person at each bank. Investments: £85,000 for each '
     'person at each firm. This covers the firm failing, not shares falling in value.',
     'https://www.fscs.org.uk/what-we-cover/', CHECKED),
    ('Stamp Duty On Purchases',
     '0.5% when buying UK shares and investment trusts. ETFs are not charged (stated by '
     'Trading 212; not stated on GOV.UK).',
     'https://www.gov.uk/tax-buy-shares', CHECKED),
    ('Spread Betting And CFDs',
     'IG publishes that 70% of retail investor accounts lose money on these.',
     'https://www.ig.com/uk/investments/share-dealing/costs-fees', CHECKED),
]
