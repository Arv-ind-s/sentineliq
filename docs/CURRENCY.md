# Display currency

SentinelPdM presents all monetary values in Indian rupees (INR), using Indian
lakh/crore digit grouping in the web interface and rehearsal output.

The existing seeded databases store their historical synthetic values in GBP.
To avoid silently changing the meaning of those persisted columns, API
responses convert amounts to INR and expose only `_inr` monetary fields. The
conversion is centralized in `core/currency.py` and uses deterministic
financial half-up rounding.

The frozen conversion rate is **£1 = ₹127.6005**, dated **27 July 2026**, from
the Bank of England daily spot exchange-rate series
[XUDLBK97](https://www.bankofengland.co.uk/boeapps/database/Rates.asp?into=GBP).
This fixed rate keeps demo and test results reproducible. Update the rate,
date, tests, and measured rehearsal values together when refreshing it.
