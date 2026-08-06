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
"""

from __future__ import annotations

from dataclasses import dataclass
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


def buy_vs_rent(price: float, area: float, params: CostParams) -> dict[str, float]:
    """Simulate 30 years of owning this flat vs renting it.

    Both scenarios start with the same liquid capital and the same monthly
    housing budget: the flat's estimated market rent (price * rental yield).
    The renter spends the whole budget on rent; the buyer pays mortgage plus
    holding costs and invests (or draws) the difference. Home value grows at
    the appreciation rate and is sold at the end, minus the resale fee. All
    cash values are deflated to today's dollars by inflation.
    """
    down_payment = price * params.down_payment_pct
    mortgage = monthly_mortgage(price, params)
    holding = estimated_holding_monthly(price, area, params)
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
