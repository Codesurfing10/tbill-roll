# tbill-roll

A small reporter for rolling 4-week (about 1-month) US Treasury bills.

## What the strategy is

Buy a 4-week Treasury bill. When it matures, the Treasury pays the face amount. Take that cash — the money you put in, plus the interest — and buy the next 4-week bill. Repeat. That reinvestment is the "recycle" or roll.

The profit is the Treasury's own yield on the bill. This is not an arbitrage and it is not a guaranteed extra spread on top of the yield. If the yield later falls, the next bill earns less. If it rises, the next bill earns more. The script's multi-roll balance assumes today's auction price never changes, which is only a projection.

T-bills are discount instruments. You pay less than face value and receive face value at maturity. There is no coupon check along the way. The auction publishes two rates:

- **High discount rate** (`high_discnt_rate`): the discount from face, quoted on a 360-day basis. This is the rate the auction "stops" at.
- **High investment rate** (`high_investment_rate`): the equivalent coupon-issue yield (bond-equivalent yield) on the money actually invested, using a 365-day year. For bills, the API's `high_yield` field is null. The investment rate is the yield this project reports.

Interest on Treasury bills is taxable at the federal level and is generally exempt from state and local income tax. This repository is not investment advice, not tax advice, and not a live trading bot. It does not connect to a brokerage and it does not place orders.

## What the script does

`roll.py` calls the public Treasury Fiscal Data auctions API (no key):

`https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query`

It filters to non-cash-management 4-week bills, takes the newest auction that already has a price and investment rate, and prints the auction date, issue date, maturity date, day count from issue to maturity, high investment rate, discount rate, and price per $100.

The roll math uses that price and the actual number of days between issue and maturity (not a hardcoded 28). Starting cash buys bills at the discount price and is redeemed at par. One-cycle interest is the discount earned on that cash. After N rolls (default 13, about a year of 4-week cycles) the balance is starting cash compounded at the same price.

## Run

Python 3 standard library only. No third-party packages.

```bash
python3 roll.py
python3 roll.py --notional 10000 --rolls 13
```

Official auction results are also posted by Treasury as PDFs under TreasuryDirect (for example the competitive-results file named in the API's `pdf_filenm_comp_results` field).

## Example output

Captured on 2026-10-03 from the live Fiscal Data API (auction date 2026-10-01, CUSIP 912797VP9). Re-run `roll.py` for the current auction; do not treat this block as a live quote.

```
4-week US Treasury bill — latest auction
CUSIP: 912797VP9
Term: 4-Week (28-Day)
Auction date: 2026-10-01
Issue date: 2026-10-06
Maturity date: 2026-11-03
Days (issue to maturity): 28
High investment rate: 3.956000% (investment yield; API field high_investment_rate)
High discount rate: 3.890000% (API field high_discnt_rate)
High yield field: n/a (null for bills) (API field high_yield)
Price per $100: 99.697444

Notional (cash invested): $10,000.00
One-cycle interest: $30.35
Balance after 13 rolls (constant price, full reinvestment): $10,401.78
```

The same auction's TreasuryDirect competitive-results PDF is [R_20261001_1.pdf](https://www.treasurydirect.gov/instit/annceresult/press/preanre/2026/R_20261001_1.pdf): high rate 3.890% (discount), investment rate 3.956% (equivalent coupon-issue yield), price 99.697444.

## Dashboard

Run the local page (localhost only):

```bash
python3 serve.py
```

Then open http://127.0.0.1:8765/ . If that port is already taken, the process stays on localhost and prints the next free port.

The page loads recent 4-week bill auctions from the same Fiscal Data API as `roll.py`, shows discount rate, investment rate, price, and interest on a cash notional (default $10,000), and reuses `roll.py` for the one-cycle interest and 13-roll balance.

A settings panel can store a broker API key and secret, with a paper/live label, in `keys.json` in this directory. That file is gitignored. The server never prints the key or secret and only returns a masked form after save. TreasuryDirect has no public trading API, so the keys are only for a later Schwab or IBKR adapter. The connection status stays "keys saved locally, trading not wired". This dashboard does not call a brokerage and does not send an order.
