from collections import OrderedDict, defaultdict
from decimal import Decimal


def clean_transactions(transactions):
    """Remove exact counter-bookings and group equal remaining positions.

    Actuals and commitments are handled independently. This prevents an
    actual payment from cancelling a commitment for the same amount.
    """
    remaining = _without_counter_bookings(transactions)
    grouped = OrderedDict()

    for transaction in remaining:
        key = (
            transaction["type"],
            transaction.get("business_partner", ""),
            transaction.get("position", ""),
        )
        if key not in grouped:
            grouped[key] = dict(transaction)
            grouped[key]["amount"] = Decimal(transaction["amount"])
        else:
            grouped[key]["amount"] += Decimal(transaction["amount"])

    return [
        transaction
        for transaction in grouped.values()
        if transaction["amount"] != 0
    ]


def clean_fund_values(values, treat_negative_actuals_as_funding=False):
    """Build display values without changing the cached SAP source data."""
    transactions = clean_transactions(values["transactions"])
    actual_total = Decimal("0")
    commitments_total = Decimal("0")
    funding_total = Decimal("0")
    displayed_transactions = []

    for transaction in transactions:
        displayed_transaction = dict(transaction)
        amount = Decimal(transaction["amount"])
        is_funding = (
            treat_negative_actuals_as_funding
            and transaction["type"] == "actual"
            and amount < 0
        )
        displayed_transaction["is_funding"] = is_funding
        displayed_transactions.append(displayed_transaction)

        if is_funding:
            funding_total += -amount
        elif transaction["type"] == "actual":
            actual_total += amount
        else:
            commitments_total += amount

    combined_total = actual_total + commitments_total
    result = dict(values)
    result.update(
        {
            "transactions": displayed_transactions,
            "actual_total": actual_total,
            "commitments_total": commitments_total,
            "combined_total": combined_total,
            "remaining": (
                values["budget"] - combined_total
                if values["has_budget"]
                else None
            ),
            "funding_total": funding_total,
        }
    )
    return result


def _without_counter_bookings(transactions):
    unmatched = defaultdict(list)
    removed = set()

    for index, transaction in enumerate(transactions):
        amount = Decimal(transaction["amount"])
        if amount == 0:
            continue
        key = (transaction["type"], amount)
        opposite_key = (transaction["type"], -amount)
        if unmatched[opposite_key]:
            opposite_index = unmatched[opposite_key].pop()
            removed.update((opposite_index, index))
        else:
            unmatched[key].append(index)

    return [
        transaction
        for index, transaction in enumerate(transactions)
        if index not in removed
    ]
