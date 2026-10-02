"""TV-L pay tables and employer cost calculation.

Tabellenentgelte are the official TV-L amounts (Anlage B, allgemeine
Tabelle). The employer contributions were derived from SAP payroll
bookings (Arbeitgeberbrutto) and reproduce them to the cent:

    Personalkosten = Brutto + VBL-Umlage
                     + SV-Sätze × (Brutto + VBL-Umlage − VBL-Freibetrag)

with health/care insurance capped at the KV/PV ceiling and pension/
unemployment insurance at the RV/AV ceiling (Beitragsbemessungsgrenzen).
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


CENT = Decimal("0.01")

# Monthly Tabellenentgelte per Entgeltgruppe, Stufe 1-6.
GROSS_TABLES = {
    "2025": {
        "E11": "4064.54 4323.79 4619.10 5068.49 5720.84 5886.14",
        "E13": "4629.74 4967.01 5220.71 5713.58 6394.91 6580.44",
        "E14": "5003.49 5365.66 5662.85 6112.24 6800.81 6998.52",
    },
    "2026": {
        "E11": "4178.35 4444.86 4748.43 5210.41 5881.02 6050.95",
        "E13": "4759.37 5106.09 5366.89 5873.56 6573.97 6764.69",
        "E14": "5143.59 5515.90 5821.41 6283.38 6991.23 7194.48",
    },
    "2027": {
        "E11": "4261.92 4533.76 4843.40 5314.62 5998.64 6171.97",
        "E13": "4854.56 5208.21 5474.23 5991.03 6705.45 6899.98",
        "E14": "5246.46 5626.22 5937.84 6409.05 7131.05 7338.37",
    },
    "2028": {
        "E11": "4304.54 4579.10 4891.83 5367.77 6058.63 6233.69",
        "E13": "4903.11 5260.29 5528.97 6050.94 6772.50 6968.98",
        "E14": "5298.92 5682.48 5997.22 6473.14 7202.36 7411.75",
    },
}


@dataclass(frozen=True)
class EmployerRates:
    """Arbeitgeberanteile in percent and monthly Beitragsbemessungsgrenzen."""

    vbl: Decimal  # Zusatzversorgung (VBL-Umlage), of the gross salary
    vbl_allowance: Decimal  # VBL-Umlage not added to the SV base (EUR)
    pension: Decimal  # Rentenversicherung
    health: Decimal  # Krankenversicherung incl. half Zusatzbeitrag
    care: Decimal  # Pflegeversicherung
    unemployment: Decimal  # Arbeitslosenversicherung
    levy: Decimal  # Umlage U2
    ceiling_pension: Decimal  # BBG RV/AV
    ceiling_health: Decimal  # BBG KV/PV


RATES_2025 = EmployerRates(
    vbl=Decimal("5.49"), vbl_allowance=Decimal("67.79"),
    pension=Decimal("9.3"), health=Decimal("8.645"), care=Decimal("1.8"), unemployment=Decimal("1.3"),
    levy=Decimal("0.42"), ceiling_pension=Decimal("8050.00"), ceiling_health=Decimal("5512.50"),
)
RATES_2026 = EmployerRates(
    vbl=Decimal("5.49"), vbl_allowance=Decimal("67.79"),
    pension=Decimal("9.3"), health=Decimal("8.645"), care=Decimal("1.8"), unemployment=Decimal("1.3"),
    levy=Decimal("0.49"), ceiling_pension=Decimal("8450.00"), ceiling_health=Decimal("5812.50"),
)

# (name, valid_from, valid_until, gross table, rates). Periods are split
# where either the TV-L table or the contribution rates change; rates for
# 2027 and later are not known yet and continue those of 2026.
PERIODS = [
    ("TV-L 2025", date(2025, 2, 1), date(2025, 12, 31), "2025", RATES_2025),
    ("TV-L 2025 (Beitragssätze 2026)", date(2026, 1, 1), date(2026, 3, 31), "2025", RATES_2026),
    ("TV-L 2026", date(2026, 4, 1), date(2027, 2, 28), "2026", RATES_2026),
    ("TV-L 2027", date(2027, 3, 1), date(2027, 12, 31), "2027", RATES_2026),
    ("TV-L 2028", date(2028, 1, 1), None, "2028", RATES_2026),
]


def _percent(base, rate):
    return (base * rate / Decimal("100")).quantize(CENT, ROUND_HALF_UP)


def rates_on(day):
    """Employer rates in effect on a day (latest known rates after the last period)."""
    for _, valid_from, valid_until, _, rates in PERIODS:
        if valid_from <= day and (valid_until is None or day <= valid_until):
            return rates
    return PERIODS[-1][4] if day > PERIODS[-1][1] else PERIODS[0][4]


def employer_cost(gross, rates):
    """Monthly Personalkosten (Arbeitgeberbrutto) for a full-time gross salary."""
    vbl = _percent(gross, rates.vbl)
    sv_base = gross + vbl - rates.vbl_allowance
    pension_base = min(sv_base, rates.ceiling_pension)
    health_base = min(sv_base, rates.ceiling_health)
    contributions = (
        _percent(pension_base, rates.pension)
        + _percent(pension_base, rates.unemployment)
        + _percent(health_base, rates.health)
        + _percent(health_base, rates.care)
        + _percent(pension_base, rates.levy)
    )
    return gross + vbl + contributions


def gross_amounts(table):
    """{(Entgeltgruppe, Stufe): gross} of a gross table."""
    return {
        (group, level): Decimal(amount)
        for group, amounts in GROSS_TABLES[table].items()
        for level, amount in enumerate(amounts.split(), start=1)
    }


# § 20 Abs. 2 TV-L: Jahressonderzahlung in percent per Entgeltgruppe.
SPECIAL_PAYMENT_RATES = [
    ({"1", "2", "3", "4"}, Decimal("87.43")),
    ({"5", "6", "7", "8"}, Decimal("88.14")),
    ({"9", "9a", "9b", "10", "11"}, Decimal("74.35")),
    ({"12", "13"}, Decimal("46.47")),
    ({"14", "15"}, Decimal("32.53")),
]


def special_payment_rate_for(name):
    """§ 20 rate for an Entgeltgruppe name like "E13", "E 9b" or "E13Ü", or None."""
    group = name.upper().replace(" ", "").removeprefix("E").removesuffix("Ü").lower()
    for groups, rate in SPECIAL_PAYMENT_RATES:
        if group in groups:
            return rate
    return None


def special_payment_cost(special_gross, regular_gross, rates):
    """Employer cost of a Jahressonderzahlung paid with a month's regular gross salary.

    One-off payments carry VBL and social insurance but no U2 levy. The
    annual contribution ceilings are approximated by twelve times the room
    left under the monthly ceiling by the regular salary.
    """
    vbl = _percent(special_gross, rates.vbl)
    sv_base = special_gross + vbl
    regular_base = regular_gross + _percent(regular_gross, rates.vbl) - rates.vbl_allowance
    pension_base = min(sv_base, max(Decimal("0"), 12 * (rates.ceiling_pension - regular_base)))
    health_base = min(sv_base, max(Decimal("0"), 12 * (rates.ceiling_health - regular_base)))
    return (
        special_gross + vbl
        + _percent(pension_base, rates.pension)
        + _percent(pension_base, rates.unemployment)
        + _percent(health_base, rates.health)
        + _percent(health_base, rates.care)
    )
