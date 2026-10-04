#!/usr/bin/env python3
"""Fetch 4-week Treasury bill auctions and project a constant-yield roll.

Uses the public Treasury Fiscal Data auctions API. No API key. Does not place trades.
"""

import argparse
import json
import sys
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
    "v1/accounting/od/auctions_query"
)

FIELDS = [
    "cusip",
    "security_type",
    "security_term",
    "security_term_day_month",
    "auction_date",
    "issue_date",
    "maturity_date",
    "high_investment_rate",
    "high_discnt_rate",
    "high_yield",
    "price_per100",
    "high_price",
    "cash_management_bill_cmb",
]

BILL_FILTER = (
    "security_type:eq:Bill,"
    "security_term:eq:4-Week,"
    "cash_management_bill_cmb:eq:No"
)


def is_missing(value):
    return value is None or str(value).strip().lower() in {"", "null", "none"}


def as_decimal(value, label):
    if is_missing(value):
        raise ValueError(f"missing {label}")
    return Decimal(str(value))


def money(amount):
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def holding_period_growth(price):
    """Cash growth from buying at the discount price and redeeming at par."""
    if price <= 0:
        raise ValueError("price must be positive")
    return Decimal("100") / price


def one_cycle_interest(price, notional):
    return money(notional * (holding_period_growth(price) - 1))


def balance_after_rolls(price, notional, rolls):
    if rolls < 1:
        raise ValueError("rolls must be at least 1")
    return money(notional * (holding_period_growth(price) ** rolls))


def _request_auctions(page_size):
    query = urlencode(
        {
            "filter": BILL_FILTER,
            "sort": "-auction_date",
            "page[size]": str(page_size),
            "fields": ",".join(FIELDS),
        }
    )
    url = f"{API_BASE}?{query}"
    request = Request(
        url,
        headers={
            "User-Agent": "tbill-roll/1.0 (public fiscal data lookup)",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return url, payload.get("data") or []


def _completed(row):
    if is_missing(row.get("high_investment_rate")) or is_missing(row.get("price_per100")):
        return False
    if is_missing(row.get("issue_date")) or is_missing(row.get("maturity_date")):
        return False
    if is_missing(row.get("auction_date")) or is_missing(row.get("cusip")):
        return False
    issue = date.fromisoformat(row["issue_date"])
    maturity = date.fromisoformat(row["maturity_date"])
    return (maturity - issue).days > 0


def fetch_auctions(limit=12):
    """Newest completed non-CMB 4-week bills, up to limit."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    page_size = min(max(limit + 5, 12), 50)
    url, rows = _request_auctions(page_size)
    completed = [row for row in rows if _completed(row)][:limit]
    if not completed:
        raise RuntimeError("No completed 4-week bill auction in the API response.")
    return url, completed


def fetch_latest():
    url, rows = fetch_auctions(1)
    return url, rows[0]


def describe_auction(row, notional):
    """One auction row plus interest on notional, using the shared price formula."""
    issue = date.fromisoformat(row["issue_date"])
    maturity = date.fromisoformat(row["maturity_date"])
    days = (maturity - issue).days
    price = as_decimal(row["price_per100"], "price_per100")
    investment = as_decimal(row["high_investment_rate"], "high_investment_rate")
    discount = (
        None
        if is_missing(row.get("high_discnt_rate"))
        else Decimal(str(row["high_discnt_rate"]))
    )
    return {
        "auction_date": row["auction_date"],
        "cusip": row["cusip"],
        "issue_date": issue.isoformat(),
        "maturity_date": maturity.isoformat(),
        "days": days,
        "discount_rate": None if discount is None else format(discount, "f"),
        "investment_rate": format(investment, "f"),
        "price_per_100": format(price, "f"),
        "interest": format(one_cycle_interest(price, notional), "f"),
        "term": row.get("security_term"),
        "term_days": row.get("security_term_day_month"),
    }


def project_latest(row, notional, rolls):
    price = as_decimal(row["price_per100"], "price_per100")
    described = describe_auction(row, notional)
    described["one_cycle_interest"] = format(one_cycle_interest(price, notional), "f")
    described["balance_after_rolls"] = format(
        balance_after_rolls(price, notional, rolls), "f"
    )
    described["rolls"] = rolls
    return described


def main():
    parser = argparse.ArgumentParser(
        description="Report the latest 4-week T-bill auction and a constant-yield roll."
    )
    parser.add_argument(
        "--notional",
        type=Decimal,
        default=Decimal("10000"),
        help="Starting cash to roll, default 10000",
    )
    parser.add_argument(
        "--rolls",
        type=int,
        default=13,
        help="Number of reinvestment cycles, default 13 (about a year of 4-week bills)",
    )
    args = parser.parse_args()
    if args.rolls < 1:
        raise SystemExit("--rolls must be at least 1")
    if args.notional <= 0:
        raise SystemExit("--notional must be positive")

    source_url, row = fetch_latest()
    latest = project_latest(row, args.notional, args.rolls)
    discount_text = (
        "n/a" if latest["discount_rate"] is None else f"{latest['discount_rate']}%"
    )
    high_yield = row.get("high_yield")
    yield_text = "n/a (null for bills)" if is_missing(high_yield) else str(high_yield)

    print("4-week US Treasury bill — latest auction")
    print(f"Source: {source_url}")
    print(f"CUSIP: {latest['cusip']}")
    print(f"Term: {latest['term']} ({latest['term_days']})")
    print(f"Auction date: {latest['auction_date']}")
    print(f"Issue date: {latest['issue_date']}")
    print(f"Maturity date: {latest['maturity_date']}")
    print(f"Days (issue to maturity): {latest['days']}")
    print(
        f"High investment rate: {latest['investment_rate']}% "
        "(investment yield; API field high_investment_rate)"
    )
    print(f"High discount rate: {discount_text} (API field high_discnt_rate)")
    print(f"High yield field: {yield_text} (API field high_yield)")
    print(f"Price per $100: {latest['price_per_100']}")
    print()
    print(f"Notional (cash invested): ${args.notional:,.2f}")
    print(f"One-cycle interest: ${Decimal(latest['one_cycle_interest']):,.2f}")
    print(
        f"Balance after {latest['rolls']} rolls "
        f"(constant price, full reinvestment): "
        f"${Decimal(latest['balance_after_rolls']):,.2f}"
    )
    print(
        "Projection assumes this auction price stays constant. "
        "It is not a forecast and not a trade."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
