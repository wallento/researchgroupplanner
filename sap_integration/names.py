import re
import unicodedata


def name_key(name):
    """Order- and spelling-insensitive key for person names.

    SAP payroll texts lose umlauts ("Wi kirchen" for "Wißkirchen"), and the
    reservation header uses "Last, First", so we compare sorted ASCII tokens.
    """
    normalized = (
        name.replace("ß", "ss")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
    )
    normalized = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode()
    tokens = re.findall(r"[a-z]+", normalized.lower())
    return " ".join(sorted(tokens))


def match_staff_member(sap_name, staff_members):
    """Return the staff member whose name matches the SAP name, if unambiguous."""
    from sap_integration.models import SAPPersonMapping

    key = name_key(sap_name)
    if not key:
        return None
    mapping = SAPPersonMapping.objects.filter(name_key=key).select_related("staff_member").first()
    if mapping:
        return mapping.staff_member
    matches = [
        member
        for member in staff_members
        if name_key(f"{member.last_name}, {member.first_name}") == key
    ]
    return matches[0] if len(matches) == 1 else None
