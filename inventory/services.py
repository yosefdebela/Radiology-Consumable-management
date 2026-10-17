import re

from django.db import transaction

from .models import (CatalogItem, Consumable, ConsumableAlternative, ExaminationConsumable,
                     catalog_key)

MAX_ALTERNATIVES = 10


def items_from_examination(source):
    """Consumable settings of `source`, ready to apply elsewhere (inactive consumables are left out)."""
    return [
        {'consumable': l.consumable, 'quantity': l.quantity, 'unit': l.unit,
         'is_required': l.is_required, 'notes': l.notes, 'replaces': l.replaces}
        for l in source.consumable_links.select_related('consumable', 'unit', 'replaces')
        if l.consumable.is_active
    ]


@transaction.atomic
def apply_items(exams, items, overwrite=False):
    """Apply consumable `items` to every examination in `exams`.

    Existing links are skipped, or updated when overwrite=True.
    Returns {'added': n, 'updated': n, 'skipped': n}.
    """
    added = updated = skipped = 0
    for exam in exams:
        existing = {l.consumable_id: l for l in exam.consumable_links.all()}
        # a "replaces" link is only kept when the replaced consumable is required on this examination
        required_here = {c for c, l in existing.items() if l.is_required} | \
                        {it['consumable'].pk for it in items if it['is_required']}
        new_links = []
        for it in items:
            if it.get('replaces') is not None and (it['is_required'] or it['replaces'].pk not in required_here):
                it = {**it, 'replaces': None}
            link = existing.get(it['consumable'].pk)
            if link is None:
                new_links.append(ExaminationConsumable(
                    examination=exam, consumable=it['consumable'], quantity=it['quantity'],
                    unit=it['unit'], is_required=it['is_required'], notes=it['notes'],
                    replaces=it.get('replaces')))
            elif overwrite:
                link.quantity, link.unit = it['quantity'], it['unit']
                link.is_required, link.notes = it['is_required'], it['notes']
                if 'replaces' in it:
                    link.replaces = it['replaces']
                link.save()
                updated += 1
            else:
                skipped += 1
        ExaminationConsumable.objects.bulk_create(new_links)
        added += len(new_links)
    return {'added': added, 'updated': updated, 'skipped': skipped}


def copy_consumables(source, target, overwrite=False):
    return apply_items([target], items_from_examination(source), overwrite=overwrite)



def items_from_set(consumable_set):
    """The consumables of a saved set, ready to apply to an examination (inactive consumables are left out)."""
    return [{'consumable': i.consumable, 'quantity': i.quantity, 'unit': i.unit,
             'is_required': i.is_required, 'notes': i.notes, 'replaces': i.replaces}
            for i in consumable_set.items.select_related('consumable', 'unit', 'replaces')
            if i.consumable.is_active]


def split_names(text):
    """'A; B | C' -> ['A', 'B', 'C'] (tidy spaces, no repeats). Used for typed and imported alternatives."""
    out, seen = [], set()
    for part in re.split(r'[;|\n]+', text or ''):
        name = ' '.join(part.split())[:150]
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


@transaction.atomic
def set_alternatives(primary, names, replace=False):
    """Make `names` the alternatives of `primary`. A name that is not a consumable yet is created
    (using the Excel list's category/unit when it is in the list, otherwise the primary's), so what the
    person typed is always accepted. With replace=True, links not in `names` are removed.
    Returns {'created': [...], 'ignored': [...], 'removed': n}."""
    created, ignored, wanted = [], [], []
    for name in names:
        if name.lower() == primary.name.lower():
            ignored.append(name)                       # an item cannot be its own alternative
        elif len(wanted) >= MAX_ALTERNATIVES:
            ignored.append(name)
        else:
            wanted.append(name)
    objs = []
    for name in wanted:
        obj = Consumable.objects.filter(name__iexact=name).first()
        if obj is None:
            ref = CatalogItem.objects.filter(name_key=catalog_key(name)).select_related('category', 'default_unit').first()
            obj = Consumable.objects.create(
                name=name,
                category=(ref.category if ref and ref.category else primary.category),
                default_unit=(ref.default_unit if ref and ref.default_unit else primary.default_unit),
                description=ref.description if ref else '')
            created.append(name)
        objs.append(obj)
    existing = {l.alternative_id: l for l in primary.alternative_links.all()}
    for obj in objs:
        if obj.pk not in existing:
            ConsumableAlternative.objects.create(consumable=primary, alternative=obj)
    removed = 0
    if replace:
        keep = {o.pk for o in objs}
        for alt_id, link in existing.items():
            if alt_id not in keep:
                link.delete()
                removed += 1
    return {'created': created, 'ignored': ignored, 'removed': removed}
