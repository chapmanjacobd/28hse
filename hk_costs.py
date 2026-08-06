#!/usr/bin/env python3
"""Hong Kong buy-vs-rent cost model used by the 28Hse buy-mode scraper.

The structure mirrors ~/bin/condo.py (monthly compounding, monthly budgets
growing with inflation, NPV costs deflated back to today's dollars) but with
Hong Kong assumptions: no capital-gains tax on residential resale, stamp
duty + agency fee as upfront purchase costs, and rates/government rent plus
the building management fee as the ongoing "HOA-like" holding costs. The
management fee is not exposed on 28Hse, so it is estimated from usable area.

The scraper imports this module to (1) skip buy listings whose estimated
monthly outlay (mortgage + holding costs) exceeds a budget, and (2) attach
cost-over-time columns to each output row.

Run the module directly to print a per-district buy-vs-rent table from the
scraper's data/28hse_buy.csv and data/28hse_rentals.csv outputs, in the
spirit of ~/bin/condo.py's decision table.
"""

from __future__ import annotations

import argparse
import csv
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CostParams:
    mortgage_rate: float = 0.0375
    mortgage_term: int = 25
    down_payment_pct: float = 0.30
    rental_yield: float = 0.025
    appreciation: float = 0.03
    inflation: float = 0.03
    stock_return: float = 0.07
    rates_pct: float = 0.0015  # annual rates + government rent, fraction of price
    management_per_sqft: float = 4.5  # estimated monthly fee per usable sqft
    management_pct: float = 0.004  # fallback fraction of price/year, no area
    maintenance_pct: float = 0.002
    purchase_fees_pct: float = 0.045  # stamp duty + agency + legal
    selling_cost_pct: float = 0.01  # resale agency fee
    horizon_years: int = 30
    total_capital: float = 1_000_000
    max_monthly_outlay: float | None = 35_000  # None disables the filter


COST_FIELDS = [
    "monthly_mortgage_hkd",
    "monthly_holding_hkd",
    "monthly_outlay_hkd",
    "initial_outlay_hkd",
    "est_market_rent_hkd",
    "npv_total_cost_buy_30y_hkd",
    "npv_total_cost_rent_30y_hkd",
    "npv_net_worth_buy_30y_hkd",
    "npv_net_worth_rent_30y_hkd",
    "buy_vs_rent_30y_hkd",
]


def monthly_payment(loan: float, annual_rate: float, months: int) -> float:
    """Amortized monthly payment for a fixed-rate loan."""
    if months <= 0 or loan <= 0:
        return 0.0
    if annual_rate == 0:
        return loan / months
    monthly_rate = annual_rate / 12
    factor = (1 + monthly_rate) ** months
    return loan * monthly_rate * factor / (factor - 1)


def monthly_mortgage(price: float, params: CostParams) -> float:
    loan = price * (1 - params.down_payment_pct)
    return monthly_payment(loan, params.mortgage_rate, params.mortgage_term * 12)


def estimated_holding_monthly(price: float, area: float, params: CostParams) -> float:
    """Rates/government rent + management fee + maintenance, per month."""
    rates = price * params.rates_pct / 12
    if area and area > 0:
        management = area * params.management_per_sqft
    else:
        management = price * params.management_pct / 12
    maintenance = price * params.maintenance_pct / 12
    return rates + management + maintenance


def estimated_rent_monthly(price: float, params: CostParams) -> float:
    return price * params.rental_yield / 12


def estimated_monthly_outlay(price: float, area: float, params: CostParams) -> float:
    return monthly_mortgage(price, params) + estimated_holding_monthly(price, area, params)


def buy_vs_rent(
    price: float, area: float, params: CostParams, rent: float | None = None
) -> dict[str, float]:
    """Simulate 30 years of owning this flat vs renting it.

    Both scenarios start with the same liquid capital and the same monthly
    housing budget: the flat's estimated market rent (price * rental yield)
    unless an observed `rent` is passed in. The renter spends the whole
    budget on rent; the buyer pays mortgage plus holding costs and invests
    (or draws) the difference. Home value grows at the appreciation rate and
    is sold at the end, minus the resale fee. All cash values are deflated
    to today's dollars by inflation.
    """
    down_payment = price * params.down_payment_pct
    mortgage = monthly_mortgage(price, params)
    holding = estimated_holding_monthly(price, area, params)
    if rent is None:
        rent = estimated_rent_monthly(price, params)

    horizon_months = params.horizon_years * 12
    stock_monthly = params.stock_return / 12
    inflation = params.inflation
    mortgage_months = params.mortgage_term * 12

    buy_stocks = params.total_capital - down_payment
    rent_stocks = params.total_capital
    npv_buy_costs = down_payment
    npv_rent_costs = 0.0

    for month in range(1, horizon_months + 1):
        buy_stocks *= 1 + stock_monthly
        rent_stocks *= 1 + stock_monthly
        year = (month - 1) // 12
        appreciation = (1 + params.appreciation) ** year
        inflation_year = (1 + inflation) ** year

        rent_curr = rent * inflation_year
        mortgage_curr = mortgage if month <= mortgage_months else 0.0
        holding_curr = holding * appreciation
        outlay_curr = mortgage_curr + holding_curr

        discount = (1 + inflation) ** (month / 12.0)
        npv_buy_costs += outlay_curr / discount
        npv_rent_costs += rent_curr / discount

        buy_stocks += rent_curr - outlay_curr

    appreciation_30 = (1 + params.appreciation) ** params.horizon_years
    inflation_30 = (1 + inflation) ** params.horizon_years
    home_value = price * appreciation_30
    net_home = home_value * (1 - params.selling_cost_pct) / inflation_30

    buy_net_worth = buy_stocks / inflation_30 + net_home
    rent_net_worth = rent_stocks / inflation_30

    return {
        "monthly_mortgage_hkd": round(mortgage),
        "monthly_holding_hkd": round(holding),
        "monthly_outlay_hkd": round(mortgage + holding),
        "initial_outlay_hkd": round(down_payment + price * params.purchase_fees_pct),
        "est_market_rent_hkd": round(rent),
        "npv_total_cost_buy_30y_hkd": round(npv_buy_costs),
        "npv_total_cost_rent_30y_hkd": round(npv_rent_costs),
        "npv_net_worth_buy_30y_hkd": round(buy_net_worth),
        "npv_net_worth_rent_30y_hkd": round(rent_net_worth),
        "buy_vs_rent_30y_hkd": round(buy_net_worth - rent_net_worth),
    }


def compute_buy_costs(listing: dict[str, Any], params: CostParams) -> dict[str, Any]:
    """Attach the cost-model columns to a listing row."""
    try:
        price = float(listing["price_hkd"])
    except (KeyError, TypeError, ValueError):
        price = 0.0
    try:
        area = float(listing["usable_area_sqft"])
    except (TypeError, ValueError):
        area = 0.0
    if price <= 0:
        return {field: "" for field in COST_FIELDS}
    return dict(buy_vs_rent(price, area, params))


def _parse_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV input does not exist: {path}")
    with path.open("r", encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def build_district_table(
    rental_rows: list[dict[str, Any]],
    buy_rows: list[dict[str, Any]],
    params: CostParams,
) -> tuple[list[str], list[list[str]], list[str]]:
    """Compare each district's median buy listing against its market rent.

    Returns (headers, table, notes) ready for tabulate, one row per district
    found in the buy CSV, sorted by buy-vs-rent net-worth delta. The rent
    scenario uses the district's observed median rent per sqft from the
    rentals CSV, scaled to the median buy flat's usable area; districts
    without rental data fall back to the overall median rent per sqft.
    """
    rent_psf_by_district: dict[str, list[float]] = {}
    all_rent_psf: list[float] = []
    for row in rental_rows:
        rent = _parse_float(row.get("price_hkd"))
        area = _parse_float(row.get("usable_area_sqft"))
        if rent > 0 and area > 0:
            rent_psf = rent / area
            rent_psf_by_district.setdefault(row.get("district", ""), []).append(rent_psf)
            all_rent_psf.append(rent_psf)

    buy_listings: dict[str, list[tuple[float, float]]] = {}
    for row in buy_rows:
        price = _parse_float(row.get("price_hkd"))
        area = _parse_float(row.get("usable_area_sqft"))
        if price > 0 and area > 0:
            buy_listings.setdefault(row.get("district", ""), []).append((price, area))

    headers = [
        "District",
        "Buy Units",
        "Median Price",
        "Monthly Outlay",
        "Market Rent",
        "NPV Buy Costs",
        "NPV Rent Costs",
        "NPV Buy Worth",
        "Buy vs Rent",
        "vs Rent",
    ]
    raw: list[tuple[str, int, float, dict[str, float], float, bool]] = []
    for district, listings in buy_listings.items():
        median_price = statistics.median([price for price, _ in listings])
        median_area = statistics.median([area for _, area in listings])
        psf_values = rent_psf_by_district.get(district)
        if psf_values:
            market_rent = statistics.median(psf_values) * median_area
            estimated = False
        elif all_rent_psf:
            market_rent = statistics.median(all_rent_psf) * median_area
            estimated = True
        else:
            market_rent = estimated_rent_monthly(median_price, params)
            estimated = True
        costs = buy_vs_rent(median_price, median_area, params, rent=market_rent)
        raw.append((district, len(listings), median_price, costs, market_rent, estimated))

    raw.sort(key=lambda item: item[3]["buy_vs_rent_30y_hkd"], reverse=True)

    table: list[list[str]] = []
    for district, units, median_price, costs, market_rent, estimated in raw:
        buy_worth = costs["npv_net_worth_buy_30y_hkd"]
        rent_worth = costs["npv_net_worth_rent_30y_hkd"]
        if rent_worth > 0:
            vs_rent = f"{(buy_worth - rent_worth) / rent_worth * 100:+.1f}%"
        else:
            vs_rent = "n/a"
        table.append(
            [
                district + (" *" if estimated else ""),
                f"{units}",
                f"${median_price:,.0f}",
                f"${costs['monthly_outlay_hkd']:,.0f}",
                f"${market_rent:,.0f}",
                f"${costs['npv_total_cost_buy_30y_hkd']:,.0f}",
                f"${costs['npv_total_cost_rent_30y_hkd']:,.0f}",
                f"${buy_worth:,.0f}",
                f"${costs['buy_vs_rent_30y_hkd']:,.0f}",
                vs_rent,
            ]
        )

    notes: list[str] = []
    if any(estimated for _, _, _, _, _, estimated in raw):
        notes.append(
            "* rent estimated from the overall rental median; no rental "
            "listings in this district"
        )
    return headers, table, notes


def main() -> None:
    from tabulate import tabulate

    parser = argparse.ArgumentParser(
        description=(
            "Per-district buy-vs-rent table from the scraper's CSV outputs, "
            "in the spirit of ~/bin/condo.py."
        )
    )
    parser.add_argument(
        "--rentals",
        type=Path,
        default=Path("data/28hse_rentals.csv"),
        help="rental listings CSV (default: data/28hse_rentals.csv)",
    )
    parser.add_argument(
        "--buy",
        type=Path,
        default=Path("data/28hse_buy.csv"),
        help="sales listings CSV with cost columns (default: data/28hse_buy.csv)",
    )
    cost_group = parser.add_argument_group("cost model")
    cost_group.add_argument(
        "--cost-capital",
        type=float,
        default=1_000_000,
        help="liquid capital available for the buy/rent comparison "
        "(default: 1000000)",
    )
    cost_group.add_argument(
        "--cost-mortgage-rate",
        type=float,
        default=0.0375,
        help="annual mortgage rate (default: 0.0375)",
    )
    cost_group.add_argument(
        "--cost-mortgage-term",
        type=int,
        default=25,
        help="mortgage term in years (default: 25)",
    )
    cost_group.add_argument(
        "--cost-down-payment",
        type=float,
        default=0.30,
        help="down payment as a fraction of price (default: 0.30)",
    )
    cost_group.add_argument(
        "--cost-rental-yield",
        type=float,
        default=0.025,
        help="assumed rental yield when no market rent is available "
        "(default: 0.025)",
    )
    cost_group.add_argument(
        "--cost-appreciation",
        type=float,
        default=0.03,
        help="annual home appreciation (default: 0.03)",
    )
    cost_group.add_argument(
        "--cost-inflation",
        type=float,
        default=0.03,
        help="annual inflation (default: 0.03)",
    )
    cost_group.add_argument(
        "--cost-stock-return",
        type=float,
        default=0.07,
        help="annual investment return (default: 0.07)",
    )
    cost_group.add_argument(
        "--cost-purchase-fees",
        type=float,
        default=0.045,
        help="stamp duty + agency + legal, as a fraction of price "
        "(default: 0.045)",
    )
    cost_group.add_argument(
        "--cost-management-sqft",
        type=float,
        default=4.5,
        help="estimated monthly building management fee per usable sqft "
        "(default: 4.5)",
    )
    args = parser.parse_args()

    params = CostParams(
        total_capital=args.cost_capital,
        mortgage_rate=args.cost_mortgage_rate,
        mortgage_term=args.cost_mortgage_term,
        down_payment_pct=args.cost_down_payment,
        rental_yield=args.cost_rental_yield,
        appreciation=args.cost_appreciation,
        inflation=args.cost_inflation,
        stock_return=args.cost_stock_return,
        purchase_fees_pct=args.cost_purchase_fees,
        management_per_sqft=args.cost_management_sqft,
    )

    headers, table, notes = build_district_table(
        _read_csv_rows(args.rentals), _read_csv_rows(args.buy), params
    )
    print(tabulate(table, headers=headers, tablefmt="simple") + "\n")
    for note in notes:
        print(note)


if __name__ == "__main__":
    main()
