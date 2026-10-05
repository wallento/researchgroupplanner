from .models import StaffMember, Employment, EmploymentSalaries
from django.contrib import messages
from decimal import Decimal
from dateutil.relativedelta import relativedelta
from calendar import monthrange


CENT = Decimal("0.01")


def get_salary_amounts_by_month(salary, period_start=None, period_end=None):
    """Return a salary record's contribution per month for an overlap period."""
    start = max(salary.start_date, period_start or salary.start_date)
    end = min(salary.end_date, period_end or salary.end_date)
    if end < start:
        return {}

    amounts = {}
    current = start.replace(day=1)
    exact_period_days = (salary.end_date - salary.start_date).days + 1
    while current <= end:
        month_end = current.replace(day=monthrange(current.year, current.month)[1])
        overlap_start = max(start, current)
        overlap_end = min(end, month_end)
        overlap_days = (overlap_end - overlap_start).days + 1
        denominator = (
            exact_period_days
            if salary.is_exact_amount
            else monthrange(current.year, current.month)[1]
        )
        amount = (
            Decimal(salary.salary)
            * Decimal(overlap_days)
            / Decimal(denominator)
        ).quantize(CENT)
        amounts[current.strftime("%Y-%m")] = amount
        current += relativedelta(months=1)
    return amounts

def get_salaries_by_month(employment: Employment):
    current = employment.start_date.replace(day=1)
    months = {}
    while current <= employment.end_date:
        months[current.strftime("%Y-%m")] = Decimal('0.00')
        current += relativedelta(months=1)

    for salary in employment.employmentsalaries_set.all().order_by('start_date'):
        for key, amount in get_salary_amounts_by_month(
            salary,
            employment.start_date,
            employment.end_date,
        ).items():
            if key in months:
                months[key] += amount

    return months


def get_sap_actuals_by_month(allocations):
    """SAP payroll actuals per month for the positions referenced by the allocations.

    Returns {"YYYY-MM": [{"project", "label", "amount", "positions"}]} summed
    per fund, using only the latest import of each fund. "positions" holds
    (fund_id, position_id, reference) of the contributing SAP positions.
    """
    from sap_integration.crosscheck import references
    from sap_integration.models import SAPPosition

    # The same SAP reference can span several employments, so only months
    # covered by an allocation carrying the reference count here.
    months_by_ref = {}
    for allocation in allocations:
        end = allocation.end_date or allocation.employment.end_date
        months = set(_month_keys(allocation.start_date, end))
        for ref in references(allocation.sap_reference):
            months_by_ref.setdefault(ref, set()).update(months)
    if not months_by_ref:
        return {}

    positions = SAPPosition.objects.filter(reference__in=months_by_ref).select_related(
        "sap_import__fund__project", "sap_import__fund__annual_pool"
    ).order_by("-sap_import__imported_at")
    latest_imports = {}
    actuals = {}
    for position in positions:
        fund = position.sap_import.fund
        latest_imports.setdefault(fund.id, position.sap_import_id)
        if latest_imports[fund.id] != position.sap_import_id:
            continue
        for month, amount in position.monthly_actuals.items():
            if month not in months_by_ref[position.reference]:
                continue
            entry = actuals.setdefault(month, {}).setdefault(fund.id, {
                "project": fund.project,
                "label": _fund_label(fund),
                "amount": Decimal("0.00"),
                "positions": set(),
            })
            entry["amount"] += Decimal(amount)
            entry["positions"].add((fund.id, position.id, position.reference))
    return {month: list(by_fund.values()) for month, by_fund in actuals.items()}


SAP_SALARY_TOLERANCE = Decimal("1.00")


def get_sap_salary_mismatches(salaries_by_month, sap_actuals):
    """Months where the planned salary differs from SAP's actual payroll (all funds combined)."""
    mismatches = []
    for month, entries in sorted(sap_actuals.items()):
        sap_total = sum((entry["amount"] for entry in entries), Decimal("0.00"))
        planned = salaries_by_month.get(month, Decimal("0.00"))
        if abs(sap_total - planned) > SAP_SALARY_TOLERANCE:
            mismatches.append({
                "month": month,
                "planned": planned,
                "sap": sap_total,
                "difference": sap_total - planned,
            })
    return mismatches


def get_sap_correction_links(sap_actuals, mismatches):
    """Reconciliation pages of the SAP positions booked in the mismatching months."""
    from django.urls import reverse

    links = {}
    for mismatch in mismatches:
        for entry in sap_actuals.get(mismatch["month"], []):
            if entry["project"] is None:
                continue
            for fund_id, position_id, reference in entry["positions"]:
                links.setdefault((fund_id, position_id), {
                    "label": f"{entry['label']} – SAP-Position {reference}",
                    "url": reverse("sap_integration:position_detail", args=[fund_id, position_id]),
                })
    return sorted(links.values(), key=lambda link: link["label"])


def _fund_label(fund):
    if fund.project_id:
        return fund.project.acronym
    if fund.annual_pool_id:
        return f"Annual Pool {fund.annual_pool.title}"
    if fund.is_universal:
        return "Universalprojekt"
    return fund.label or fund.fund_number


def _month_keys(start, end):
    current = start.replace(day=1)
    while current <= end:
        yield current.strftime("%Y-%m")
        current += relativedelta(months=1)


def level_defaults(staff_member):
    """Default TV-L classification for a new employment of an existing person.

    Continues the most recent employment with level data; otherwise Stufe 1
    from the start of the person's first employment. None without employments.
    """
    employments = sorted(staff_member.employment_set.all(), key=lambda e: e.start_date)
    if not employments:
        return None
    with_level = [e for e in employments if e.start_level and e.level_start_date]
    latest = with_level[-1] if with_level else None
    category_source = latest or next((e for e in reversed(employments) if e.salary_category_id), None)
    return {
        "start_level": latest.start_level if latest else 1,
        "level_start_date": (latest.level_start_date if latest else employments[0].start_date).isoformat(),
        "salary_category": category_source.salary_category_id if category_source else None,
    }


def special_payment(employment, year):
    """Projected Jahressonderzahlung (§ 20 TV-L) of an employment for a year.

    Returns {"gross", "cost"} or None. Entitled is who is employed on
    1 December. The base is the average monthly table pay of July to
    September, or the first full calendar month if the employment started
    after 31 August; each month of the year without pay reduces it by a
    twelfth. Consecutive employments of the person count as one.
    """
    from datetime import date, timedelta

    from .tvl import employment_rates, special_payment_cost

    rate = employment.salary_category.special_payment_rate if employment.salary_category_id else None
    december = date(year, 12, 1)
    if rate is None or not (employment.start_date <= december <= employment.end_date):
        return None

    # Employments of the person that form one continuous employment relationship.
    chain = [employment]
    others = sorted(employment.staff_member.employment_set.exclude(pk=employment.pk), key=lambda e: e.start_date)
    for other in reversed(others):
        if other.end_date < chain[0].start_date and other.end_date + timedelta(days=1) >= chain[0].start_date:
            chain.insert(0, other)
    relationship_start = chain[0].start_date

    def full_month_gross(month_start):
        month_end = _month_end(month_start.strftime("%Y-%m"))
        for part in chain:
            if part.start_date <= month_start and part.end_date >= month_end:
                # Earlier parts without pay grade use this employment's classification.
                gross = part.tariff_gross_at(month_start)
                if gross is None and part is not employment:
                    gross = employment.tariff_gross_at(month_start)
                return gross
        return None

    base_months = []
    if relationship_start <= date(year, 8, 31):
        base_months = [g for g in (full_month_gross(date(year, m, 1)) for m in (7, 8, 9)) if g is not None]
    if not base_months:
        first_full = relationship_start if relationship_start.day == 1 else (
            relationship_start.replace(day=1) + relativedelta(months=1)
        )
        gross = full_month_gross(first_full)
        base_months = [gross] if gross is not None else []
    if not base_months:
        return None
    base = sum(base_months) / len(base_months)

    paid_months = sum(
        1 for m in range(1, 13)
        if any(
            part.start_date <= _month_end(f"{year}-{m:02d}") and part.end_date >= date(year, m, 1)
            for part in chain
        )
    )
    special_gross = (base * rate / 100 * paid_months / 12).quantize(CENT)
    november = date(year, 11, 1)
    regular_gross = employment.tariff_gross_at(max(november, employment.start_date)) or base
    return {
        "gross": special_gross,
        "cost": special_payment_cost(special_gross, regular_gross, employment_rates(employment, november)),
    }


def apply_tariff_salaries(employment, from_month=None):
    """Fill the employment's salaries from the TV-L tables, incl. Jahressonderzahlung in November.

    Months before from_month ("YYYY-MM") keep their current amounts. Full
    months become monthly rates (equal consecutive months merged); partial
    months and November with the Jahressonderzahlung become exact amounts.
    Returns the months without a table amount, which also keep their amounts.
    """
    from datetime import date

    from .models import EmploymentSalaries

    existing = get_salaries_by_month(employment)
    months = []  # (first_day, last_day, amount, is_rate)
    missing = []
    for month in _month_keys(employment.start_date, employment.end_date):
        month_start = date.fromisoformat(f"{month}-01")
        first_day = max(month_start, employment.start_date)
        last_day = min(_month_end(month), employment.end_date)
        is_full = first_day == month_start and last_day == _month_end(month)
        if from_month and month < from_month:
            amount, is_rate = existing.get(month, Decimal("0.00")), is_full
        else:
            rate = employment.tariff_amount_at(first_day)
            if rate is None:
                missing.append(month)
                amount, is_rate = existing.get(month, Decimal("0.00")), is_full
            else:
                days = monthrange(month_start.year, month_start.month)[1]
                amount = rate if is_full else (rate * ((last_day - first_day).days + 1) / days).quantize(CENT)
                is_rate = is_full
                bonus = special_payment(employment, month_start.year) if month_start.month == 11 else None
                if bonus:
                    amount += bonus["cost"]
                    is_rate = False
        if amount:
            months.append((first_day, last_day, amount, is_rate))

    records = []
    for first_day, last_day, amount, is_rate in months:
        previous = records[-1] if records else None
        if (
            is_rate and previous and not previous.is_exact_amount and previous.salary == amount
            and previous.end_date + relativedelta(days=1) == first_day
        ):
            previous.end_date = last_day
            continue
        records.append(EmploymentSalaries(
            employment=employment, salary=amount, start_date=first_day, end_date=last_day,
            is_exact_amount=not is_rate,
        ))

    employment.employmentsalaries_set.all().delete()
    EmploymentSalaries.objects.bulk_create(records)
    return missing


def _previous_month_key(month):
    from datetime import date

    return (date.fromisoformat(f"{month}-01") - relativedelta(months=1)).strftime("%Y-%m")


def _month_end(month):
    from datetime import date

    first = date.fromisoformat(f"{month}-01")
    return first.replace(day=monthrange(first.year, first.month)[1])


def rebooking_cost_deltas(rebookings=None):
    """{staff budget item id: cost change} if the open Umbuchungen were applied."""
    from projects.utils import calculate_salary_for_allocation

    from .models import Rebooking

    if rebookings is None:
        rebookings = Rebooking.objects.select_related("allocation__employment")
    deltas = {}
    for rebooking in rebookings:
        source, target = rebooking.as_allocations()
        if source.budget_item_id:
            deltas[source.budget_item_id] = (
                deltas.get(source.budget_item_id, Decimal("0.00")) - calculate_salary_for_allocation(source).salary_sum
            )
        deltas[target.budget_item_id] = (
            deltas.get(target.budget_item_id, Decimal("0.00")) + calculate_salary_for_allocation(target).salary_sum
        )
    return {item_id: delta for item_id, delta in deltas.items() if delta}


def rebooking_person_month_deltas(rebookings=None):
    """{staff budget item id: person month change} if the open Umbuchungen were applied."""
    from projects.utils import get_allocation_person_months

    from .models import Rebooking

    if rebookings is None:
        rebookings = Rebooking.objects.select_related("allocation__employment")
    deltas = {}
    for rebooking in rebookings:
        source, target = rebooking.as_allocations()
        for allocation, sign in ((source, -1), (target, 1)):
            if allocation.budget_item_id:
                pm = sum(get_allocation_person_months(allocation).values(), Decimal("0"))
                deltas[allocation.budget_item_id] = deltas.get(allocation.budget_item_id, Decimal("0")) + sign * pm
    return deltas


def employment_merge_check(first, second):
    """Whether two employments can be merged; returns (ok, reason).

    Mergeable are back-to-back employments of the same person with the same
    percentage, category, status and pay grade. Level data must be equal or
    set on one side only with a level start not after the merged start.
    """
    from datetime import timedelta

    if first.staff_member_id != second.staff_member_id:
        return False, "Die Anstellungen gehören zu verschiedenen Personen."
    if first.end_date + timedelta(days=1) != second.start_date:
        return False, "Die Anstellungen schließen nicht direkt aneinander an."
    for field, label in (("percentage", "Umfang"), ("category", "Kategorie"), ("status", "Status")):
        if getattr(first, field) != getattr(second, field):
            return False, f"{label} unterscheidet sich ({_merge_value(first, field)} / {_merge_value(second, field)})."
    if first.salary_category_id and second.salary_category_id and first.salary_category_id != second.salary_category_id:
        return False, f"Entgeltgruppe unterscheidet sich ({first.salary_category} / {second.salary_category})."
    first_level = (first.start_level, first.level_start_date)
    second_level = (second.start_level, second.level_start_date)
    if first.start_level and second.start_level and first_level != second_level:
        return False, (
            f"Stufen unterscheiden sich (Stufe {first.start_level} ab {first.level_start_date:%d.%m.%Y} / "
            f"Stufe {second.start_level} ab {second.level_start_date:%d.%m.%Y})."
        )
    level_start = first.level_start_date or second.level_start_date
    if level_start and level_start > first.start_date:
        return False, (
            f"Der Stufenbeginn {level_start:%d.%m.%Y} liegt nach dem Beginn der zusammengeführten Anstellung "
            f"({first.start_date:%d.%m.%Y}); bitte Stufe und Stufenbeginn vorher angleichen."
        )
    return True, None


def _merge_value(employment, field):
    if field == "category":
        return employment.get_category()
    if field == "status":
        return employment.get_status_display()
    return f"{Decimal(employment.percentage).normalize():f} %"


def employment_merge_candidates(employments):
    """[(first, second, ok, reason)] for back-to-back employments of the same person."""
    from datetime import timedelta

    ordered = sorted(employments, key=lambda e: (e.staff_member_id, e.start_date))
    candidates = []
    for first, second in zip(ordered, ordered[1:]):
        if first.staff_member_id == second.staff_member_id and first.end_date + timedelta(days=1) == second.start_date:
            ok, reason = employment_merge_check(first, second)
            candidates.append((first, second, ok, reason))
    return candidates


def merge_employments(first, second):
    """Merge the following employment into the first one; costs and allocations stay the same."""
    from django.db import transaction

    ok, reason = employment_merge_check(first, second)
    if not ok:
        raise ValueError(reason)
    with transaction.atomic():
        # Open-ended allocations of the first employment would otherwise grow with it.
        first.stafffundingallocation_set.filter(end_date__isnull=True).update(end_date=first.end_date)
        second.stafffundingallocation_set.update(employment=first)
        second.employmentsalaries_set.update(employment=first)
        first.end_date = second.end_date
        if not first.salary_category_id:
            first.salary_category_id = second.salary_category_id
        if not first.start_level:
            first.start_level, first.level_start_date = second.start_level, second.level_start_date
        first.save()
        second.delete()
    return first


def rebooking_effects(rebookings=None):
    """[(budget item id, sign, SalaryAllocation)] of the open Umbuchungen.

    Each Umbuchung removes its share from the source budget (sign -1) and adds
    it to the target budget (sign +1); the SalaryAllocation carries the monthly
    costs and the unsaved allocation for person months.
    """
    from projects.utils import calculate_salary_for_allocation

    from .models import Rebooking

    if rebookings is None:
        rebookings = Rebooking.objects.select_related("allocation__employment")
    effects = []
    for rebooking in rebookings:
        source, target = rebooking.as_allocations()
        for allocation, sign in ((source, -1), (target, 1)):
            if allocation.budget_item_id:
                effects.append((allocation.budget_item_id, sign, calculate_salary_for_allocation(allocation)))
    return effects


def staff_reservations(staff_member):
    """SAP Mittelreservierungen (staff positions) of a person from the latest import of each project fund.

    A reservation belongs to the person if one of their allocations carries
    its SAP reference ("linked") or, failing that, if the SAP name matches
    ("by name"; link_candidates are allocations it could be linked to).
    """
    from datetime import date

    from django.urls import reverse

    from projects.models import SAPFund
    from sap_integration.crosscheck import build_reconciliation, contract_periods, references
    from sap_integration.names import match_staff_member

    from .models import StaffFundingAllocation, StaffMember

    allocations = list(
        StaffFundingAllocation.objects.filter(employment__staff_member=staff_member).select_related("employment")
    )
    own_refs = set()
    for allocation in allocations:
        own_refs |= references(allocation.sap_reference)
    all_staff = list(StaffMember.objects.all())

    result = []
    for fund in SAPFund.objects.filter(project__isnull=False, sap_imports__isnull=False).select_related("project").distinct():
        reconciliation = None
        sap_import = fund.sap_imports.order_by("-imported_at").first()
        for position in sap_import.positions.filter(kind="staff"):
            if position.is_transfer or not (position.contract_periods or position.commitment):
                continue
            linked = position.reference in own_refs
            if not linked and (
                not position.person_name or match_staff_member(position.person_name, all_staff) != staff_member
            ):
                continue
            if reconciliation is None:
                reconciliation = build_reconciliation(fund)
            check = next((c for c in reconciliation.checks if c.position.id == position.id), None)
            periods = contract_periods(position)
            candidates = [] if linked else [
                a for a in allocations
                if not references(a.sap_reference) and any(
                    a.start_date <= end and start <= (a.end_date or a.employment.end_date) for start, end in periods
                )
            ]
            result.append({
                "fund": fund,
                "project": fund.project,
                "position": position,
                "url": reverse("sap_integration:position_detail", args=[fund.id, position.id]),
                "periods": periods,
                "linked": linked,
                "link_candidates": candidates,
                "issues": [issue["text"] for issue in check.obligo_issues] if check else [],
                "ignored": bool(check and check.ignored),
            })
    return sorted(result, key=lambda r: (min((s for s, _ in r["periods"]), default=r["position"].first_date or date.max)))
