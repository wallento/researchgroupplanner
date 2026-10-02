from django import forms

from projects.models import EmploymentCategories, OtherBudgetItem, OtherBudgetItemTransaction, StaffBudgetItem
from staffing.models import Employment, StaffFundingAllocation, StaffMember


class ExportUploadForm(forms.Form):
    export_file = forms.FileField(
        label="SAP-Export (GM_E_4GBA, .xlsx)",
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"}),
    )


class IgnoreForm(forms.Form):
    note = forms.CharField(label="Notiz", max_length=255, required=False)


class StaffTransformForm(forms.Form):
    staff_member = forms.ModelChoiceField(
        StaffMember.objects.order_by("last_name", "first_name"),
        label="Mitarbeiter",
        required=False,
        empty_label="– neu anlegen –",
    )
    first_name = forms.CharField(label="Vorname (neu)", max_length=100, required=False)
    last_name = forms.CharField(label="Nachname (neu)", max_length=100, required=False)
    remember_name = forms.BooleanField(
        label="SAP-Namen für künftige Importe diesem Mitarbeiter zuordnen",
        required=False,
        initial=True,
    )
    employment = forms.ModelChoiceField(
        Employment.objects.none(),
        label="Anstellung",
        required=False,
        empty_label="– neue Anstellung je SAP-Vertragszeitraum –",
        help_text="Direkt anschließende Vertragszeiträume verlängern die gewählte Anstellung.",
    )
    budget_item = forms.ModelChoiceField(StaffBudgetItem.objects.none(), label="Personalbudget")
    category = forms.ChoiceField(label="Kategorie", choices=list(EmploymentCategories.items()))
    percentage = forms.DecimalField(label="Umfang (%)", max_digits=5, decimal_places=2, min_value=0, max_value=100)
    create_salaries = forms.BooleanField(
        label="Gehälter aus SAP übernehmen (Ist je Monat, Restmonate mit letztem Monatsgehalt)",
        required=False,
        initial=True,
    )

    def __init__(self, *args, project, staff_member=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["budget_item"].queryset = StaffBudgetItem.objects.filter(project=project)
        employments = Employment.objects.select_related("staff_member").order_by("staff_member__last_name", "start_date")
        if staff_member is not None:
            employments = employments.filter(staff_member=staff_member)
        self.fields["employment"].queryset = employments

    def clean(self):
        cleaned = super().clean()
        staff_member = cleaned.get("staff_member")
        employment = cleaned.get("employment")
        if staff_member is None and not (cleaned.get("first_name") and cleaned.get("last_name")):
            raise forms.ValidationError("Bitte einen Mitarbeiter wählen oder Vor- und Nachname angeben.")
        if employment is not None and employment.staff_member_id != getattr(staff_member, "id", None):
            raise forms.ValidationError("Die gewählte Anstellung gehört nicht zum gewählten Mitarbeiter.")
        return cleaned


class LinkAllocationsForm(forms.Form):
    allocations = forms.ModelMultipleChoiceField(
        StaffFundingAllocation.objects.none(),
        label="Zuordnungen",
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["allocations"].queryset = StaffFundingAllocation.objects.filter(
            budget_item__project=project
        ).select_related("employment__staff_member").order_by("employment__staff_member__last_name", "start_date")


class CreateTransactionForm(forms.Form):
    budget_item = forms.ModelChoiceField(OtherBudgetItem.objects.none(), label="Sachmittelbudget")

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["budget_item"].queryset = OtherBudgetItem.objects.filter(project=project)


class LinkTransactionForm(forms.Form):
    transaction = forms.ModelChoiceField(OtherBudgetItemTransaction.objects.none(), label="Planungseintrag")

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["transaction"].queryset = OtherBudgetItemTransaction.objects.filter(
            budget_item__project=project
        ).select_related("budget_item").order_by("date")


class CostTypeMappingForm(forms.Form):
    cost_type = forms.CharField(max_length=50, widget=forms.HiddenInput)
    target = forms.ChoiceField(label="Budget", required=False)

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].choices = budget_choices(project)


def budget_choices(project):
    choices = [("", "– keine Zuordnung –")]
    choices += [
        (f"staff:{item.id}", f"Personal: {item.title}")
        for item in StaffBudgetItem.objects.filter(project=project)
    ]
    choices += [
        (f"other:{item.id}", f"Sachmittel: {item.title}")
        for item in OtherBudgetItem.objects.filter(project=project)
    ]
    return choices
