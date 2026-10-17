"""One 'Save changes' for a whole page: every edited row, any number of new items and the page's other pending
choices, all together or not at all."""
import re
from django.db import transaction

from .models import Consumable

_NEW = re.compile(r'^(new\d+)-consumable$')
_TRUE = ('on', 'true', '1', 'yes')


def is_on(request, key):
    return str(request.POST.get(key, '')).strip().lower() in _TRUE


def new_prefixes(request):
    """Prefixes of the 'new item' rows that have a consumable chosen (new0, new3...). Blank rows are ignored."""
    found = {m.group(1) for k, v in request.POST.items() if (m := _NEW.match(k)) and v.strip()}
    return sorted(found, key=lambda p: int(p[3:]))


def collect(request, links, update_cls, letter, add_cls, add_kwargs):
    """Read a Save-changes post. Existing rows are recognised by their prefixed fields (l12-quantity, s7-quantity),
    new rows by new0-, new1-... Rows ticked "Remove" are removed on save, and a row's "use instead" choice swaps it
    for an alternative. A replacement may point at any consumable that will be required once everything is saved,
    including one that is being added in the same click."""
    removed = [l for l in links if is_on(request, f'{letter}{l.pk}-remove')]
    swaps = {}
    for l in links:
        value = request.POST.get(f'{letter}{l.pk}-swap', '')
        if value.isdigit() and l not in removed:
            swaps[l.pk] = int(value)
    posted = [l for l in links if f'{letter}{l.pk}-quantity' in request.POST and l not in removed]
    prefixes = new_prefixes(request)

    required_after = {l.consumable_id for l in links if l not in posted and l not in removed and l.is_required}
    required_after |= {l.consumable_id for l in posted if is_on(request, f'{letter}{l.pk}-is_required')}
    required_after |= {swaps[l.pk] for l in posted if l.pk in swaps and is_on(request, f'{letter}{l.pk}-is_required')}
    for p in prefixes:
        cid = request.POST.get(f'{p}-consumable', '')
        if cid.isdigit() and is_on(request, f'{p}-is_required'):
            required_after.add(int(cid))

    # an optional item that replaced something now being removed simply loses that link
    data = request.POST.copy()
    gone = {str(l.consumable_id) for l in removed}
    for key in list(data.keys()):
        if key.endswith('-replaces') and data.get(key) in gone:
            data[key] = ''

    forms = {}
    for l in posted:
        f = update_cls(data, instance=l, prefix=f'{letter}{l.pk}')
        f.fields['replaces'].queryset = Consumable.objects.filter(pk__in=required_after).exclude(pk=l.consumable_id)
        forms[l.pk] = f
    new_forms = []
    for p in prefixes:
        f = add_cls(data, prefix=p, **add_kwargs)
        f.fields['replaces'].queryset = Consumable.objects.filter(pk__in=required_after)
        new_forms.append(f)

    seen = set()
    for f in new_forms:                                  # the same consumable on two new rows
        if f.is_valid():
            c = f.cleaned_data['consumable']
            if c.pk in seen:
                f.add_error('consumable', 'This consumable is listed twice.')
            seen.add(c.pk)
    valid = all(f.is_valid() for f in list(forms.values()) + new_forms)
    return {'posted': posted, 'forms': forms, 'new_forms': new_forms, 'valid': valid,
            'removed': removed, 'swaps': swaps}


def error_messages(result, name_of):
    out = []
    for link in result['posted']:
        for errs in result['forms'][link.pk].errors.values():
            out.append(f'{name_of(link)}: {" ".join(errs)}')
    for i, f in enumerate(result['new_forms'], start=1):
        for errs in f.errors.values():
            out.append(f'New item {i}: {" ".join(errs)}')
    return out


def commit(result, attach, swap=None, unlink=None):
    """Save everything in one transaction: changed rows, swaps, removals, then new items.
    Returns {'updated': n, 'swapped': n, 'removed': n, 'added': [objects]}."""
    out = {'updated': 0, 'swapped': 0, 'removed': 0, 'added': []}
    with transaction.atomic():
        for form in result['forms'].values():
            if form.has_changed():
                form.save()
                out['updated'] += 1
        for link, alt in result.get('swap_pairs', []):
            swap(link, alt)
            out['swapped'] += 1
        for link in result['removed']:
            if unlink:
                unlink(link)
            link.delete()
            out['removed'] += 1
        for form in result['new_forms']:
            obj = form.save(commit=False)
            attach(obj)
            obj.save()
            out['added'].append(obj)
    return out


def summary(done, extras=()):
    parts = []
    if done['updated']:
        parts.append(f'{done["updated"]} row{"s" if done["updated"] != 1 else ""} updated')
    if done['swapped']:
        parts.append(f'{done["swapped"]} swapped for an alternative')
    if done['removed']:
        parts.append(f'{done["removed"]} removed')
    if done['added']:
        parts.append(f'{len(done["added"])} added ({", ".join(a.consumable.name for a in done["added"])})')
    parts.extend(extras)
    return ', '.join(parts)
