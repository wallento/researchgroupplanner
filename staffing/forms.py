from django import forms

from projects.models import EmploymentCategories, StaffBudgetItem

from .models import EMPLOYMENT_STATUSES, MAX_LEVEL, SalaryCategory, StaffMember


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class PlanEmploymentForm(forms.Form):
    staff_member = forms.ModelChoiceField(
        StaffMember.objects.order_by("last_name", "first_name"),
        label="Person",
        required=False,
        empty_label="– neue Person –",
    )
    first_name = forms.CharField(label="Vorname (neue Person)", required=False)
    last_name = forms.CharField(label="Nachname (neue Person)", required=False)
    budget_item = forms.ModelChoiceField(
        StaffBudgetItem.objects.select_related("project").order_by("project__acronym", "title"),
        label="Projekt / Personalbudget",
    )
    percentage = forms.DecimalField(label="Umfang (%)", min_value=1, max_value=100, decimal_places=2, initial=100)
    start_date = forms.DateField(label="Beginn", widget=DateInput())
    end_date = forms.DateField(label="Ende", widget=DateInput())
    category = forms.ChoiceField(label="Kategorie", choices=EmploymentCategories.items(), initial="researcher")
    status = forms.ChoiceField(label="Status", choices=EMPLOYMENT_STATUSES.items(), initial="planned")
    salary_category = forms.ModelChoiceField(SalaryCategory.objects.all(), label="Entgeltgruppe", required=False)
    start_level = forms.TypedChoiceField(
        label="Stufe bei Beginn",
        choices=[("", "–")] + [(level, f"Stufe {level}") for level in range(1, MAX_LEVEL + 1)],
        coerce=int,
        empty_value=None,
        required=False,
        initial=1,
    )
    level_start_date = forms.DateField(
        label="Stufenbeginn (fiktiv)",
        widget=DateInput(),
        required=False,
        help_text="Vorbelegt aus früheren Anstellungen, für neue Personen der Beginn der Anstellung.",
    )

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        if project is not None:
            self.fields["budget_item"].queryset = self.fields["budget_item"].queryset.filter(project=project)
        self.fields["budget_item"].label_from_instance = lambda item: f"{item.project.acronym} – {item.title}"

    def clean(self):
        data = super().clean()
        if not data.get("staff_member") and not (data.get("first_name") and data.get("last_name")):
            raise forms.ValidationError("Bitte eine Person auswählen oder Vor- und Nachname einer neuen Person angeben.")
        start, end = data.get("start_date"), data.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "Das Ende darf nicht vor dem Beginn liegen.")
        if data.get("salary_category") and not data.get("start_level"):
            self.add_error("start_level", "Bitte die Stufe zur Entgeltgruppe angeben.")
        if data.get("start_level") and not data.get("level_start_date"):
            data["level_start_date"] = start
        level_start = data.get("level_start_date")
        if level_start and start and level_start > start:
            self.add_error("level_start_date", "Der Stufenbeginn darf nicht nach dem Beginn der Anstellung liegen.")
        return data
