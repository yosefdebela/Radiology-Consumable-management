"""Saved consumable sets: build a named list once, apply it to examinations as needed."""
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import ConsumableSetForm, SetItemAddForm, SetItemUpdateForm
from .models import Consumable, ConsumableSet, ConsumableSetItem, Examination, Unit
from . import saveall
from .views import prep_new_form
from .permissions import can_change, can_delete, change_required, delete_required, edit_required
from .services import apply_items, items_from_set


def set_list(request):
    qs = ConsumableSet.objects.annotate(n_items=Count('items')).order_by('name')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
    status = request.GET.get('status', 'active')
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    return render(request, 'inventory/set_list.html', {'sets': qs, 'q': q, 'status': status})


@edit_required
def set_create(request):
    form = ConsumableSetForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        s = form.save()
        messages.success(request, f'Set "{s.name}" created. Now add its consumables.')
        return redirect(s)
    return render(request, 'inventory/set_form.html', {'form': form, 'title': 'New consumable set'})


@change_required
def set_edit(request, pk):
    s = get_object_or_404(ConsumableSet, pk=pk)
    form = ConsumableSetForm(request.POST or None, instance=s)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Set updated.')
        return redirect(s)
    return render(request, 'inventory/set_form.html', {'form': form, 'title': f'Edit {s.name}', 'set': s})


def _set_page(request, s, forms=None, new_forms=None, dirty=False, posted=None, removed=()):
    forms = forms or {}
    items = s.items.select_related('consumable', 'consumable__category', 'unit', 'replaces')
    new_forms = new_forms or [SetItemAddForm(consumable_set=s, prefix='new0')]
    rows = [(i, forms[i.pk] if i.pk in forms else SetItemUpdateForm(instance=i, prefix=f's{i.pk}')) for i in items]
    for i, f in rows:
        f.remove_checked = i.pk in {r.pk for r in removed}
    return render(request, 'inventory/set_detail.html', {
        'set': s, 'rows': rows, 'dirty_start': dirty, 'posted': posted or {}, 'form_id': 'setform',
        'new_forms': [prep_new_form(f, 'setform') for f in new_forms],
        'tpl_form': prep_new_form(SetItemAddForm(consumable_set=s, prefix='new__i__'), 'setform'),
        'consumable_count': Consumable.objects.filter(is_active=True).count(),
        'consumable_units': {c.pk: c.default_unit_id for c in Consumable.objects.filter(is_active=True)},
    })


def set_detail(request, pk):
    return _set_page(request, get_object_or_404(ConsumableSet, pk=pk))


@edit_required
@require_POST
def set_save_all(request, pk):
    """The single Save button of a set: edited rows, rows ticked Remove and any number of new consumables."""
    s = get_object_or_404(ConsumableSet, pk=pk)
    items = list(s.items.select_related('consumable', 'unit', 'replaces'))
    result = saveall.collect(request, items, SetItemUpdateForm, 's', SetItemAddForm, {'consumable_set': s})
    if result['posted'] and not can_change(request.user):
        messages.error(request, 'Changing consumables that were already entered needs permission. Nothing was saved.')
        return redirect(s)
    if result['removed'] and not can_delete(request.user):
        messages.error(request, 'Removing consumables needs permission to delete. Nothing was saved.')
        return redirect(s)
    if not result['valid']:
        for msg in saveall.error_messages(result, lambda i: i.consumable.name):
            messages.error(request, msg)
        messages.error(request, 'Nothing was saved. Fix the marked items and press Save changes again.')
        return _set_page(request, s, result['forms'], result['new_forms'], dirty=True, posted=request.POST,
                         removed=result['removed'])

    def unlink(item):
        s.items.filter(replaces=item.consumable).update(replaces=None)

    done = saveall.commit(result, lambda obj: setattr(obj, 'consumable_set', s), unlink=unlink)
    text = saveall.summary(done)
    if text:
        messages.success(request, f'Saved: {text}.')
    else:
        messages.info(request, 'No changes to save.')
    return redirect(s)


@edit_required
@require_POST
def set_item_add(request, pk):
    s = get_object_or_404(ConsumableSet, pk=pk)
    form = SetItemAddForm(request.POST, consumable_set=s)
    if form.is_valid():
        item = form.save(commit=False)
        item.consumable_set = s
        item.save()
        messages.success(request, f'Added {item.consumable.name} to the set.')
    else:
        messages.error(request, 'Could not add: ' + '; '.join(f'{f}: {" ".join(e)}' for f, e in form.errors.items()))
    return redirect(s)


@change_required
@require_POST
def set_item_update(request, pk):
    item = get_object_or_404(ConsumableSetItem.objects.select_related('consumable_set', 'consumable'), pk=pk)
    form = SetItemUpdateForm(request.POST, instance=item, prefix=f's{item.pk}')
    if form.is_valid():
        form.save()
        messages.success(request, f'Updated {item.consumable.name}.')
    else:
        messages.error(request, 'Could not update: ' + '; '.join(f'{f}: {" ".join(e)}' for f, e in form.errors.items()))
    return redirect(item.consumable_set)


@delete_required
@require_POST
def set_item_remove(request, pk):
    item = get_object_or_404(ConsumableSetItem.objects.select_related('consumable_set', 'consumable'), pk=pk)
    s, name = item.consumable_set, item.consumable.name
    item.delete()
    messages.success(request, f'Removed {name} from the set.')
    return redirect(s)


@change_required
@require_POST
def set_toggle(request, pk):
    s = get_object_or_404(ConsumableSet, pk=pk)
    s.is_active = not s.is_active
    s.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, f'"{s.name}" {"activated" if s.is_active else "deactivated"}.')
    return redirect(s)


@delete_required
@require_POST
def set_delete(request, pk):
    s = get_object_or_404(ConsumableSet, pk=pk)
    name = s.name
    s.delete()          # applying a set copies its items, so examinations that used it are not affected
    messages.success(request, f'Set "{name}" deleted. Examinations that already used it keep their consumables.')
    return redirect('set_list')


@edit_required
@require_POST
def exam_apply_set(request, pk):
    exam = get_object_or_404(Examination, pk=pk)
    s = ConsumableSet.objects.filter(pk=request.POST.get('set', 0) if request.POST.get('set', '').isdigit() else 0,
                                     is_active=True).first()
    if s is None:
        messages.error(request, 'Choose a saved set to apply.')
        return redirect(exam)
    items = items_from_set(s)
    left_out = s.items.count() - len(items)
    result = apply_items([exam], items)
    note = f' ({result["skipped"]} already on this examination, left as they are)' if result['skipped'] else ''
    note += f' ({left_out} inactive consumable(s) left out)' if left_out else ''
    messages.success(request, f'Applied set "{s.name}": {result["added"]} consumable(s) added{note}.')
    return redirect(exam)


@edit_required
@require_POST
def exam_save_as_set(request, pk):
    exam = get_object_or_404(Examination, pk=pk)
    name = ' '.join(request.POST.get('name', '').split())[:120]
    links = list(exam.consumable_links.all())
    if not name:
        messages.error(request, 'Give the set a name.')
    elif not links:
        messages.error(request, 'This examination has no consumables to save yet.')
    elif ConsumableSet.objects.filter(name__iexact=name).exists():
        messages.error(request, f'A set named "{name}" already exists. Choose another name.')
    else:
        with transaction.atomic():
            s = ConsumableSet.objects.create(name=name, description=f'Saved from {exam.name}'[:255])
            ConsumableSetItem.objects.bulk_create([
                ConsumableSetItem(consumable_set=s, consumable=l.consumable, quantity=l.quantity, unit=l.unit,
                                  is_required=l.is_required, replaces=l.replaces, notes=l.notes) for l in links])
        messages.success(request, f'Saved {len(links)} consumable(s) as the set "{name}".')
        return redirect(s)
    return redirect(exam)
