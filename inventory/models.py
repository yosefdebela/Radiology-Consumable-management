from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Max
from django.urls import reverse
from django.utils.text import slugify

# Colours handed out (in turn) to modalities created from the UI.
MODALITY_PALETTE = ['#2a7f4f', '#a1455e', '#546e7a', '#3f51b5', '#c0572b', '#00838f', '#7b5e2f', '#8e44ad']


def whole_number_message(unit):
    return (f'Use a whole number for {unit.name}. To allow decimals, turn on '
            f'"Allow decimal quantities" for this unit under Setup > Units.')


def parse_quantity(raw, unit):
    """Validate a typed quantity against a unit. Returns a Decimal or raises ValidationError."""
    try:
        q = Decimal(str(raw).strip())
        if not q.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        raise ValidationError('Enter a number.')
    if q <= 0:
        raise ValidationError('Quantity must be greater than zero.')
    if q >= 1000000:
        raise ValidationError('Quantity is too large.')
    if not unit.allow_decimals:
        if q != q.to_integral_value():
            raise ValidationError(whole_number_message(unit))
        return q.quantize(Decimal('1'))
    return q.quantize(Decimal('0.01'))


class Modality(models.Model):
    """Major modality / category: CT, MRI, Ultrasound, X-Ray, Special X-Ray."""
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    color = models.CharField(
        max_length=7, default='#6b7f84',
        validators=[RegexValidator(r'^#[0-9a-fA-F]{6}$', 'Use a hex colour such as #2d68b3.')],
        help_text='Used for the sidebar dot, chips and dashboard bars.')

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = 'modality'
        verbose_name_plural = 'modalities'

    @classmethod
    def create_with_defaults(cls, name):
        """Create a modality from just a name: unique slug, next sort position, next palette colour."""
        base = slugify(name) or 'modality'
        slug, n = base, 2
        while cls.objects.filter(slug=slug).exists():
            slug, n = f'{base}-{n}', n + 1
        order = (cls.objects.aggregate(m=Max('sort_order'))['m'] or 0) + 1
        color = MODALITY_PALETTE[(cls.objects.count()) % len(MODALITY_PALETTE)]
        return cls.objects.create(name=name, slug=slug, sort_order=order, color=color)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('modality_exam_list', args=[self.slug])


class Unit(models.Model):
    name = models.CharField(max_length=30, unique=True)
    allow_decimals = models.BooleanField(
        default=False, help_text='Off: quantities in this unit must be whole numbers (1, 2, 3...). '
                                 'On: decimals such as 0.5 or 2.5 are accepted.')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ConsumableCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'consumable categories'

    def __str__(self):
        return self.name


class Examination(models.Model):
    code = models.CharField(max_length=30, blank=True, db_index=True)
    name = models.CharField(max_length=255)
    modality = models.ForeignKey(Modality, on_delete=models.PROTECT, related_name='examinations')
    subcategory = models.CharField(max_length=120, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['modality__sort_order', 'name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('exam_detail', args=[self.pk])


class Consumable(models.Model):
    name = models.CharField(max_length=150, unique=True)
    category = models.ForeignKey(ConsumableCategory, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='consumables')
    default_unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name='+')
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def is_in_use(self):
        """Used anywhere (an examination, a saved set, or as somebody's alternative): never hard-delete then."""
        return (self.examination_links.exists() or self.set_items.exists()
                or self.alternative_for_links.exists())


class ExaminationConsumable(models.Model):
    examination = models.ForeignKey(Examination, on_delete=models.CASCADE, related_name='consumable_links')
    # PROTECT: a consumable that is referenced can never be hard-deleted; deactivate it instead.
    consumable = models.ForeignKey(Consumable, on_delete=models.PROTECT, related_name='examination_links')
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=1)  # whole numbers unless unit.allow_decimals
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name='+')
    is_required = models.BooleanField(default=True, help_text='Untick for an optional consumable.')
    # For an optional consumable: the required consumable of the same examination it can replace when that
    # one is unavailable. Used to line replacements up next to their required item in the Excel exam view.
    replaces = models.ForeignKey(Consumable, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                                 help_text='Optional items only: the required consumable this can replace.')
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-is_required', 'consumable__name']
        constraints = [
            models.UniqueConstraint(fields=['examination', 'consumable'], name='unique_consumable_per_exam'),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name='quantity_positive'),
        ]

    def clean(self):
        super().clean()
        unit = self.unit if self.unit_id else None
        if unit is not None and self.quantity is not None and not unit.allow_decimals:
            q = Decimal(str(self.quantity))
            if q != q.to_integral_value():
                raise ValidationError({'quantity': whole_number_message(unit)})

    def __str__(self):
        return f'{self.examination} - {self.consumable} ({self.quantity} {self.unit})'


class ConsumableAlternative(models.Model):
    """`alternative` can be used instead of `consumable` (e.g. another brand or size) when needed."""
    consumable = models.ForeignKey(Consumable, on_delete=models.CASCADE, related_name='alternative_links')
    alternative = models.ForeignKey(Consumable, on_delete=models.PROTECT, related_name='alternative_for_links')

    class Meta:
        ordering = ['alternative__name']
        constraints = [
            models.UniqueConstraint(fields=['consumable', 'alternative'], name='unique_alternative_per_consumable'),
            models.CheckConstraint(condition=~models.Q(consumable=models.F('alternative')),
                                   name='alternative_is_not_itself'),
        ]

    def __str__(self):
        return f'{self.alternative} instead of {self.consumable}'


class ConsumableSet(models.Model):
    """A named, reusable list of consumables built independently of any examination."""
    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('set_detail', args=[self.pk])


class ConsumableSetItem(models.Model):
    consumable_set = models.ForeignKey(ConsumableSet, on_delete=models.CASCADE, related_name='items')
    consumable = models.ForeignKey(Consumable, on_delete=models.PROTECT, related_name='set_items')
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name='+')
    is_required = models.BooleanField(default=True)
    replaces = models.ForeignKey(Consumable, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                                 help_text='Optional items only: the required consumable this can replace.')
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-is_required', 'consumable__name']
        constraints = [
            models.UniqueConstraint(fields=['consumable_set', 'consumable'], name='unique_consumable_per_set'),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name='set_quantity_positive'),
        ]

    def clean(self):
        super().clean()
        unit = self.unit if self.unit_id else None
        if unit is not None and self.quantity is not None and not unit.allow_decimals:
            q = Decimal(str(self.quantity))
            if q != q.to_integral_value():
                raise ValidationError({'quantity': whole_number_message(unit)})

    def __str__(self):
        return f'{self.consumable_set}: {self.consumable} ({self.quantity} {self.unit})'


class UserProfile(models.Model):
    """What a signed-in person may do. Administrators (superusers) can always do everything."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='inventory_profile')
    can_edit = models.BooleanField(default=False, help_text='May add data.')
    can_change = models.BooleanField(default=False, help_text='May change consumables that were already entered.')
    can_delete = models.BooleanField(default=False, help_text='May delete data (requires editing).')

    def __str__(self):
        return f'Profile of {self.user}'


def catalog_key(name):
    return ' '.join(str(name).split()).lower()


class CatalogItem(models.Model):
    """Reference list of consumables (from Excel) used to prompt and auto-fill when adding a consumable."""
    name = models.CharField(max_length=150)
    name_key = models.CharField(max_length=150, unique=True, editable=False)
    category = models.ForeignKey(ConsumableCategory, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='+')
    default_unit = models.ForeignKey(Unit, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    description = models.CharField(max_length=255, blank=True)
    alternatives = models.TextField(blank=True, help_text='Alternative item names, separated by semicolons.')

    class Meta:
        ordering = ['name']

    def save(self, *args, **kwargs):
        self.name = ' '.join(self.name.split())
        self.name_key = catalog_key(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
