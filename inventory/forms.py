from django import forms
from .models import (Consumable, ConsumableCategory, ConsumableSet, ConsumableSetItem, Examination,
                     ExaminationConsumable, Modality, Unit, whole_number_message)
from .services import copy_consumables


class UnitSelect(forms.Select):
    """Unit dropdown whose options carry data-dec so the page can switch the quantity box between whole numbers and decimals."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opt = super().create_option(name, value, label, selected, index, subindex, attrs)
        key = getattr(value, 'value', value)
        if key not in (None, ''):
            opt['attrs']['data-dec'] = '1' if key in getattr(self, 'decimal_ids', ()) else '0'
        return opt


def unit_select(field):
    field.widget = UnitSelect(attrs={'class': 'input unit-sel'})
    field.widget.choices = field.choices
    field.widget.decimal_ids = set(Unit.objects.filter(allow_decimals=True).values_list('pk', flat=True))


class ExamChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.modality.name}: {obj.name}'


def exams_with_consumables(exclude=None):
    qs = (Examination.objects.filter(consumable_links__isnull=False).distinct()
          .select_related('modality').order_by('modality__sort_order', 'name'))
    return qs.exclude(pk=exclude.pk) if exclude is not None and exclude.pk else qs


class StyledMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault('class', 'check')
            else:
                w.attrs.setdefault('class', 'input')


class ExaminationForm(StyledMixin, forms.ModelForm):
    new_modality = forms.CharField(
        max_length=60, required=False, label='Or create a new modality',
        help_text='Type a name to create a new modality. If filled in, it is used instead of the choice above.')
    copy_from = ExamChoiceField(
        queryset=Examination.objects.none(), required=False, label='Copy consumables from (optional)',
        empty_label='Start with no consumables',
        help_text='Saves typing: the new examination starts with the same consumables as the one you pick.')

    field_order = ['name', 'code', 'modality', 'new_modality', 'subcategory', 'price', 'notes',
                   'copy_from', 'is_active']

    class Meta:
        model = Examination
        fields = ['name', 'code', 'modality', 'subcategory', 'price', 'notes', 'is_active']
        widgets = {'notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._new_modality_name = None
        self.fields['modality'].required = False
        self.fields['modality'].empty_label = 'Choose a modality'
        if self.instance.pk:
            del self.fields['copy_from']
        else:
            self.fields['copy_from'].queryset = exams_with_consumables()
            for name in ('copy_from',):
                self.fields[name].widget.attrs.setdefault('class', 'input')

    def clean(self):
        cd = super().clean()
        new = ' '.join((cd.get('new_modality') or '').split())
        if new:
            existing = Modality.objects.filter(name__iexact=new).first()
            cd['modality'] = existing
            self._new_modality_name = None if existing else new
        elif not cd.get('modality') and 'modality' not in self.errors:
            self.add_error('modality', 'Choose a modality or type a new one.')
        return cd

    def save(self, commit=True):
        if self._new_modality_name:
            self.instance.modality = Modality.create_with_defaults(self._new_modality_name)
        elif self.cleaned_data.get('modality'):
            self.instance.modality = self.cleaned_data['modality']
        exam = super().save(commit=commit)
        source = self.cleaned_data.get('copy_from')
        if commit and source:
            self.copied = copy_consumables(source, exam)
        return exam


class ModalityForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Modality
        fields = ['name', 'color', 'sort_order']
        widgets = {'color': forms.TextInput(attrs={'type': 'color'})}
        labels = {'sort_order': 'Position in the sidebar'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['color'].widget.attrs['class'] = 'input color'

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        clash = Modality.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError('A modality with this name already exists.')
        return name


class ConsumableForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Consumable
        fields = ['name', 'category', 'default_unit', 'description', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['default_unit'].queryset = Unit.objects.filter(is_active=True)
        if self.instance.pk:  # keep a currently-set inactive unit selectable
            self.fields['default_unit'].queryset |= Unit.objects.filter(pk=self.instance.default_unit_id)


def _no_duplicate_consumable(self):
    name = ' '.join(self.cleaned_data['name'].split())
    clash = Consumable.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
    if clash.exists():
        raise forms.ValidationError('A consumable with this name already exists.')
    return name


ConsumableForm.clean_name = _no_duplicate_consumable


class CategoryForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ConsumableCategory
        fields = ['name']


class UnitForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Unit
        fields = ['name', 'allow_decimals', 'is_active']
        labels = {'allow_decimals': 'Allow decimal quantities'}

    def clean(self):
        cd = super().clean()
        if self.instance.pk and not cd.get('allow_decimals', True):
            fractional = [l for l in ExaminationConsumable.objects.filter(unit=self.instance)
                          if l.quantity != l.quantity.to_integral_value()]
            if fractional:
                self.add_error('allow_decimals',
                               f'{len(fractional)} examination consumable(s) use a decimal quantity in {self.instance.name} '
                               f'(for example {fractional[0].examination.name}). Change those to whole numbers first.')
        return cd


def required_consumables_of(exam, exclude=None):
    """Consumables marked required on this examination: what an optional item can be set to replace."""
    if exam is None or not exam.pk:
        return Consumable.objects.none()
    qs = Consumable.objects.filter(examination_links__examination=exam, examination_links__is_required=True)
    return qs.exclude(pk=exclude.pk) if exclude is not None else qs


def _clean_replaces(form):
    cd = form.cleaned_data
    rep = cd.get('replaces')
    if cd.get('is_required'):
        cd['replaces'] = None                      # only optional items replace something
    elif rep is not None and cd.get('consumable') is not None and rep.pk == cd['consumable'].pk:
        form.add_error('replaces', 'An item cannot replace itself.')
    return cd


class AddConsumableToExamForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ExaminationConsumable
        fields = ['consumable', 'quantity', 'unit', 'is_required', 'replaces', 'notes']

    def clean(self):
        super().clean()
        return _clean_replaces(self)

    def __init__(self, *args, exam=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.exam = exam
        self.fields['replaces'].queryset = required_consumables_of(exam)
        self.fields['replaces'].empty_label = 'Not linked'
        self.fields['replaces'].label = 'Replaces'
        used = exam.consumable_links.values_list('consumable_id', flat=True) if exam else []
        self.fields['consumable'].queryset = Consumable.objects.filter(is_active=True).exclude(pk__in=used)
        self.fields['unit'].queryset = Unit.objects.filter(is_active=True)
        unit_select(self.fields['unit'])
        self.fields['quantity'].initial = 1
        self.fields['quantity'].widget.attrs.update({'step': '1', 'min': '1', 'class': 'input qty'})

    def clean_quantity(self):
        q = self.cleaned_data['quantity']
        if q is None or q <= 0:
            raise forms.ValidationError('Quantity must be greater than zero.')
        return q


class UpdateLinkForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ExaminationConsumable
        fields = ['quantity', 'unit', 'is_required', 'replaces', 'notes']

    def clean(self):
        super().clean()
        self.cleaned_data.setdefault('consumable', self.instance.consumable)
        return _clean_replaces(self)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['replaces'].queryset = required_consumables_of(self.instance.examination,
                                                                   exclude=self.instance.consumable)
        self.fields['replaces'].empty_label = 'Not linked'
        self.fields['unit'].queryset = Unit.objects.filter(is_active=True) | Unit.objects.filter(
            pk=self.instance.unit_id)

    def clean_quantity(self):
        q = self.cleaned_data['quantity']
        if q is None or q <= 0:
            raise forms.ValidationError('Quantity must be greater than zero.')
        return q


class CopyFromForm(StyledMixin, forms.Form):
    source = ExamChoiceField(queryset=Examination.objects.none(), label='Copy consumables from')

    def __init__(self, *args, exam, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['source'].queryset = exams_with_consumables(exclude=exam)


class ConsumableSetForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ConsumableSet
        fields = ['name', 'description', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            del self.fields['is_active']

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        if ConsumableSet.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('A set with this name already exists.')
        return name


def required_in_set(consumable_set, exclude=None):
    if consumable_set is None or not consumable_set.pk:
        return Consumable.objects.none()
    qs = Consumable.objects.filter(set_items__consumable_set=consumable_set, set_items__is_required=True)
    return qs.exclude(pk=exclude.pk) if exclude is not None else qs


class SetItemAddForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ConsumableSetItem
        fields = ['consumable', 'quantity', 'unit', 'is_required', 'replaces', 'notes']

    def clean(self):
        super().clean()
        return _clean_replaces(self)

    def __init__(self, *args, consumable_set=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['replaces'].queryset = required_in_set(consumable_set)
        self.fields['replaces'].empty_label = 'Not linked'
        self.fields['replaces'].label = 'Replaces'
        used = consumable_set.items.values_list('consumable_id', flat=True) if consumable_set else []
        self.fields['consumable'].queryset = Consumable.objects.filter(is_active=True).exclude(pk__in=used)
        self.fields['unit'].queryset = Unit.objects.filter(is_active=True)
        unit_select(self.fields['unit'])
        self.fields['quantity'].initial = 1
        self.fields['quantity'].widget.attrs.update({'step': '1', 'min': '1', 'class': 'input qty'})

    def clean_quantity(self):
        q = self.cleaned_data['quantity']
        if q is None or q <= 0:
            raise forms.ValidationError('Quantity must be greater than zero.')
        return q


class SetItemUpdateForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = ConsumableSetItem
        fields = ['quantity', 'unit', 'is_required', 'replaces', 'notes']

    def clean(self):
        super().clean()
        self.cleaned_data.setdefault('consumable', self.instance.consumable)
        return _clean_replaces(self)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['replaces'].queryset = required_in_set(self.instance.consumable_set,
                                                           exclude=self.instance.consumable)
        self.fields['replaces'].empty_label = 'Not linked'
        self.fields['unit'].queryset = Unit.objects.filter(is_active=True) | Unit.objects.filter(
            pk=self.instance.unit_id)

    def clean_quantity(self):
        q = self.cleaned_data['quantity']
        if q is None or q <= 0:
            raise forms.ValidationError('Quantity must be greater than zero.')
        return q
