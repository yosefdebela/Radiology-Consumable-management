import json

from django.core.exceptions import ValidationError

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.db.models.functions import Lower
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import permissions, saveall
from .permissions import change_required, delete_required, edit_required
from .catalog import build_template, import_catalog
from .exports import build_exam_view, build_workbook, exam_view_filename, export_filename
from .forms import (AddConsumableToExamForm, CategoryForm, ConsumableForm,
                    CopyFromForm, ExaminationForm, ModalityForm, UnitForm, UpdateLinkForm)
from .models import (CatalogItem, Consumable, ConsumableAlternative, ConsumableSet, ConsumableSetItem, ConsumableCategory, Examination,
                     ExaminationConsumable, Modality, Unit, parse_quantity)
from .services import (apply_items, copy_consumables, items_from_examination, items_from_set,
                       set_alternatives, split_names)


def _back(request, fallback):
    nxt = request.POST.get('next') or request.GET.get('next')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect(fallback)


def _paginate(request, qs, per_page=40):
    return Paginator(qs, per_page).get_page(request.GET.get('page'))


# ---------------------------------------------------------------- dashboard
def dashboard(request):
    active = Examination.objects.filter(is_active=True)
    modalities = Modality.objects.annotate(
        total=Count('examinations', filter=Q(examinations__is_active=True)),
        with_cons=Count('examinations', filter=Q(examinations__is_active=True,
                                                 examinations__consumable_links__isnull=False),
                        distinct=True),
    ).order_by('sort_order')
    missing = active.filter(consumable_links__isnull=True)
    total = active.count()
    missing_count = missing.count()
    context = {
        'total_exams': total,
        'inactive_exams': Examination.objects.filter(is_active=False).count(),
        'modalities': [
            {'obj': m, 'total': m.total, 'covered': m.with_cons,
             'pct': round(100 * m.with_cons / m.total) if m.total else 0}
            for m in modalities],
        'total_consumables': Consumable.objects.filter(is_active=True).count(),
        'inactive_consumables': Consumable.objects.filter(is_active=False).count(),
        'missing_count': missing_count,
        'covered_pct': round(100 * (total - missing_count) / total) if total else 0,
        'missing_sample': missing.select_related('modality')[:8],
    }
    return render(request, 'inventory/dashboard.html', context)


# ------------------------------------------------------------- examinations
def exam_list(request, slug=None):
    modality = get_object_or_404(Modality, slug=slug) if slug else None
    qs = Examination.objects.select_related('modality').annotate(n_cons=Count('consumable_links')).order_by('modality__sort_order', 'name')
    if modality:
        qs = qs.filter(modality=modality)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(subcategory__icontains=q))
    status = request.GET.get('status', 'active')
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    missing = request.GET.get('missing') == '1'
    if missing:
        qs = qs.filter(n_cons=0)
    page = _paginate(request, qs)
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'inventory/exam_list.html', {
        'modality': modality, 'page': page, 'q': q, 'status': status, 'missing': missing,
        'querystring': params.urlencode(),
    })


@edit_required
def exam_create(request):
    initial = {}
    if request.GET.get('modality'):
        initial['modality'] = Modality.objects.filter(slug=request.GET['modality']).first()
    form = ExaminationForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        exam = form.save()
        copied = getattr(form, 'copied', None)
        if copied:
            messages.success(request, f'Examination "{exam.name}" added with {copied["added"]} consumable(s) copied. '
                                      'Adjust quantities below if needed.')
        else:
            messages.success(request, f'Examination "{exam.name}" added. Now add its consumables.')
        return redirect(exam)
    return render(request, 'inventory/exam_form.html', {'form': form, 'title': 'Add examination'})


@edit_required
def exam_edit(request, pk):
    exam = get_object_or_404(Examination, pk=pk)
    form = ExaminationForm(request.POST or None, instance=exam)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Examination updated.')
        return redirect(exam)
    return render(request, 'inventory/exam_form.html',
                  {'form': form, 'title': f'Edit {exam.name}', 'exam': exam})


@edit_required
@require_POST
def exam_toggle(request, pk):
    exam = get_object_or_404(Examination, pk=pk)
    exam.is_active = not exam.is_active
    exam.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, f'"{exam.name}" {"activated" if exam.is_active else "deactivated"}.')
    return _back(request, exam)


def prep_new_form(f, form_id):
    """Make a 'new item' row part of the page's single form and give its controls names for screen readers."""
    for name, field in f.fields.items():
        field.widget.attrs['form'] = form_id
        field.widget.attrs.setdefault('aria-label', str(field.label or name).capitalize())
    f.use_required_attribute = False                    # the server checks everything; no silent browser block
    f.fields['consumable'].widget.attrs['class'] = 'input newcons'
    f.fields['replaces'].widget.attrs.update({'class': 'input replaces-sel',
                                              'data-value': str(f['replaces'].value() or '')})
    return f


def _exam_page(request, exam, forms=None, new_forms=None, dirty=False, posted=None, removed=(), swaps=None):
    """The examination page. After a failed Save changes it is shown again with what the person typed."""
    forms = forms or {}
    swaps = swaps or {}
    links = (exam.consumable_links.select_related('consumable', 'consumable__category', 'unit', 'replaces')
             .prefetch_related('consumable__alternative_links__alternative'))
    new_forms = new_forms or [AddConsumableToExamForm(exam=exam, prefix='new0')]
    rows = [(l, forms[l.pk] if l.pk in forms else UpdateLinkForm(instance=l, prefix=f'l{l.pk}')) for l in links]
    for l, f in rows:
        f.remove_checked = l.pk in {r.pk for r in removed}
        f.swap_value = str(swaps.get(l.pk, ''))
    return render(request, 'inventory/exam_detail.html', {
        'exam': exam, 'rows': rows, 'dirty_start': dirty, 'posted': posted or {}, 'form_id': 'examform',
        'new_forms': [prep_new_form(f, 'examform') for f in new_forms],
        'tpl_form': prep_new_form(AddConsumableToExamForm(exam=exam, prefix='new__i__'), 'examform'),
        'copy_form': CopyFromForm(exam=exam),
        'consumable_count': Consumable.objects.filter(is_active=True).count(),
        'units': Unit.objects.filter(is_active=True),
        'consumable_units': {c.pk: c.default_unit_id for c in Consumable.objects.filter(is_active=True)},
        'sets': ConsumableSet.objects.filter(is_active=True, items__isnull=False).distinct(),
    })


def exam_detail(request, pk):
    return _exam_page(request, get_object_or_404(Examination.objects.select_related('modality'), pk=pk))


@edit_required
@require_POST
def exam_save_all(request, pk):
    """The single Save button: edited rows, rows ticked Remove, "use instead" swaps, any number of new items
    (required, optional, replacements) and the page's other choices (apply a saved set, copy from an examination,
    save as a set). All or nothing."""
    exam = get_object_or_404(Examination.objects.select_related('modality'), pk=pk)
    links = list(exam.consumable_links.select_related('consumable', 'unit', 'replaces')
                 .prefetch_related('consumable__alternative_links__alternative'))
    result = saveall.collect(request, links, UpdateLinkForm, 'l', AddConsumableToExamForm, {'exam': exam})
    user = request.user
    if (result['posted'] or result['swaps']) and not permissions.can_change(user):
        messages.error(request, 'Changing consumables that were already entered needs permission. Nothing was saved.')
        return redirect(exam)
    if result['removed'] and not permissions.can_delete(user):
        messages.error(request, 'Removing consumables needs permission to delete. Nothing was saved.')
        return redirect(exam)
    errors = saveall.error_messages(result, lambda l: l.consumable.name)

    # swaps: only to an active alternative of that consumable that is not already on the examination
    by_pk = {l.pk: l for l in links}
    gone = {l.consumable_id for l in result['removed']}
    taken = {l.consumable_id for l in links if l.consumable_id not in gone}
    taken |= {f.cleaned_data['consumable'].pk for f in result['new_forms'] if f.is_valid()}
    result['swap_pairs'] = []
    for link_pk, alt_id in result['swaps'].items():
        link = by_pk[link_pk]
        alt = next((a.alternative for a in link.consumable.alternative_links.all() if a.alternative_id == alt_id), None)
        if alt is None or not alt.is_active:
            errors.append(f'{link.consumable.name}: that is not an active alternative of it.')
        elif alt.pk in taken:
            errors.append(f'{link.consumable.name}: {alt.name} is already on this examination.')
        else:
            taken.add(alt.pk)
            result['swap_pairs'].append((link, alt))

    chosen_set = source = None
    if request.POST.get('apply_set', '').isdigit():
        chosen_set = ConsumableSet.objects.filter(pk=request.POST['apply_set'], is_active=True).first()
        if chosen_set is None:
            errors.append('The saved set to apply was not found.')
    if request.POST.get('copy_from', '').isdigit():
        source = Examination.objects.filter(pk=request.POST['copy_from']).exclude(pk=exam.pk).first()
        if source is None:
            errors.append('The examination to copy from was not found.')
    set_name = ' '.join(request.POST.get('save_as_set', '').split())[:120]
    if set_name:
        if ConsumableSet.objects.filter(name__iexact=set_name).exists():
            errors.append(f'A set named "{set_name}" already exists. Choose another name.')
        elif not ((len(links) - len(result['removed'])) or result['new_forms'] or chosen_set or source):
            errors.append('There are no consumables to save as a set yet.')

    if errors or not result['valid']:
        for msg in errors:
            messages.error(request, msg)
        messages.error(request, 'Nothing was saved. Fix the marked items and press Save changes again.')
        return _exam_page(request, exam, result['forms'], result['new_forms'], dirty=True, posted=request.POST,
                          removed=result['removed'], swaps=result['swaps'])

    def swap(link, alt):
        old = link.consumable
        link.consumable = alt
        link.save(update_fields=['consumable'])
        exam.consumable_links.filter(replaces=old).update(replaces=alt)       # keep replacements attached

    def unlink(link):
        exam.consumable_links.filter(replaces=link.consumable).update(replaces=None)

    extras = []
    with transaction.atomic():
        done = saveall.commit(result, lambda obj: setattr(obj, 'examination', exam), swap=swap, unlink=unlink)
        if chosen_set:
            r = apply_items([exam], items_from_set(chosen_set))
            extras.append(f'set "{chosen_set.name}" applied ({r["added"]} added)')
        if source:
            r = copy_consumables(source, exam)
            extras.append(f'{r["added"]} copied from {source.name}')
        if set_name:
            made = ConsumableSet.objects.create(name=set_name, description=f'Saved from {exam.name}'[:255])
            ConsumableSetItem.objects.bulk_create([
                ConsumableSetItem(consumable_set=made, consumable=l.consumable, quantity=l.quantity, unit=l.unit,
                                  is_required=l.is_required, replaces=l.replaces, notes=l.notes)
                for l in exam.consumable_links.all()])
            extras.append(f'saved as the set "{set_name}"')
    text = saveall.summary(done, extras)
    if text:
        messages.success(request, f'Saved: {text}.')
    else:
        messages.info(request, 'No changes to save.')
    return redirect(exam)


@edit_required
@require_POST
def link_add(request, pk):
    exam = get_object_or_404(Examination, pk=pk)
    form = AddConsumableToExamForm(request.POST, exam=exam)
    if form.is_valid():
        link = form.save(commit=False)
        link.examination = exam
        link.save()
        messages.success(request, f'Added {link.consumable.name} to {exam.name}.')
    else:
        messages.error(request, 'Could not add consumable: ' + '; '.join(
            f'{f}: {" ".join(e)}' for f, e in form.errors.items()))
    return redirect(exam)


@change_required
@require_POST
def link_update(request, pk):
    link = get_object_or_404(ExaminationConsumable.objects.select_related('examination', 'consumable'), pk=pk)
    form = UpdateLinkForm(request.POST, instance=link, prefix=f'l{link.pk}')
    if form.is_valid():
        form.save()
        messages.success(request, f'Updated {link.consumable.name}.')
    else:
        messages.error(request, 'Could not update: ' + '; '.join(
            f'{f}: {" ".join(e)}' for f, e in form.errors.items()))
    return redirect(link.examination)


@delete_required
@require_POST
def link_remove(request, pk):
    link = get_object_or_404(ExaminationConsumable.objects.select_related('examination', 'consumable'), pk=pk)
    exam, name = link.examination, link.consumable.name
    exam.consumable_links.filter(replaces=link.consumable).update(replaces=None)   # nothing left to replace
    link.delete()
    messages.success(request, f'Removed {name} from {exam.name}.')
    return redirect(exam)


@change_required
@require_POST
def link_swap(request, pk):
    """Replace the consumable on an examination with one of its alternatives (quantity, unit and notes are kept)."""
    link = get_object_or_404(ExaminationConsumable.objects.select_related('examination', 'consumable'), pk=pk)
    alt = get_object_or_404(Consumable, pk=request.POST.get('alternative', 0)) if request.POST.get('alternative', '').isdigit() else None
    exam = link.examination
    if alt is None or not link.consumable.alternative_links.filter(alternative=alt).exists():
        messages.error(request, 'That is not an alternative of this consumable.')
    elif not alt.is_active:
        messages.error(request, f'"{alt.name}" is inactive, so it cannot be used.')
    elif exam.consumable_links.filter(consumable=alt).exists():
        messages.error(request, f'"{alt.name}" is already on this examination.')
    else:
        old = link.consumable.name
        exam.consumable_links.filter(replaces=link.consumable).update(replaces=alt)   # keep replacements attached
        link.consumable = alt
        link.save(update_fields=['consumable'])
        messages.success(request, f'Replaced {old} with {alt.name}. Quantity and unit were kept: check they suit it.')
    return redirect(exam)


@edit_required
@require_POST
def exam_copy(request, pk):
    exam = get_object_or_404(Examination, pk=pk)
    form = CopyFromForm(request.POST, exam=exam)
    if not form.is_valid():
        messages.error(request, 'Choose an examination to copy from.')
        return redirect(exam)
    source = form.cleaned_data['source']
    result = copy_consumables(source, exam)
    messages.success(request, f'Copied {result["added"]} consumable(s) from {source.name}'
                              + (f' ({result["skipped"]} already present, left unchanged).' if result['skipped'] else '.'))
    return redirect(exam)


@delete_required
@require_POST
def exam_delete(request, pk):
    exam = get_object_or_404(Examination, pk=pk)
    if exam.is_active:
        messages.error(request, 'Deactivate the examination first. Only inactive examinations can be deleted.')
        return redirect(exam)
    name, n = exam.name, exam.consumable_links.count()
    exam.delete()
    messages.success(request, f'"{name}" deleted' + (f' together with its {n} consumable assignment(s).' if n else '.'))
    return redirect('exam_list')


# ------------------------------------------------ apply to many examinations
def _parse_rows(post):
    """Read the repeated consumable rows. Returns (rows_for_redisplay, items, errors)."""
    cons_ids = post.getlist('row_consumable')
    qtys, units = post.getlist('row_quantity'), post.getlist('row_unit')
    reqs, notes = post.getlist('row_required'), post.getlist('row_notes')
    rows, items, errors, seen = [], [], [], set()
    for i, cid in enumerate(cons_ids):
        row = {'consumable': cid, 'quantity': qtys[i] if i < len(qtys) else '1',
               'unit': units[i] if i < len(units) else '', 'required': (reqs[i] if i < len(reqs) else '1'),
               'notes': (notes[i] if i < len(notes) else '').strip()}
        rows.append(row)
        if not cid:
            continue
        label = f'Row {i + 1}'
        consumable = Consumable.objects.filter(pk=cid, is_active=True).first() if cid.isdigit() else None
        unit = Unit.objects.filter(pk=row['unit'], is_active=True).first() if row['unit'].isdigit() else None
        qty = None
        if unit:
            try:
                qty = parse_quantity(row['quantity'], unit)
            except ValidationError as e:
                errors.append(f'{label}: {e.messages[0]}')
                continue
        if not consumable:
            errors.append(f'{label}: choose an active consumable.')
        elif not unit:
            errors.append(f'{label}: choose a unit.')
        elif consumable.pk in seen:
            errors.append(f'{label}: {consumable.name} appears more than once.')
        else:
            seen.add(consumable.pk)
            items.append({'consumable': consumable, 'quantity': qty, 'unit': unit,
                          'is_required': row['required'] == '1', 'notes': row['notes'][:255]})
    return rows, items, errors


@edit_required
def bulk_apply(request):
    exams = (Examination.objects.filter(is_active=True).select_related('modality')
             .annotate(n_cons=Count('consumable_links')).order_by('modality__sort_order', 'name'))
    ctx = {
        'exams': exams,
        'modalities': Modality.objects.all(),
        'consumables': Consumable.objects.filter(is_active=True).select_related('default_unit'),
        'units': Unit.objects.filter(is_active=True),
        'sources': Examination.objects.filter(consumable_links__isnull=False).distinct()
                   .select_related('modality').order_by('modality__sort_order', 'name'),
        'sets': ConsumableSet.objects.filter(is_active=True, items__isnull=False).distinct(),
    }
    ctx['consumable_units'] = {c.pk: c.default_unit_id for c in ctx['consumables']}

    if request.method == 'POST':
        selected_ids = [int(i) for i in request.POST.getlist('exams') if i.isdigit()]
        rows, items, errors = _parse_rows(request.POST)
        source = None
        if request.POST.get('source', '').isdigit():
            source = Examination.objects.filter(pk=request.POST['source']).first()
        chosen_set = None
        if request.POST.get('set', '').isdigit():
            chosen_set = ConsumableSet.objects.filter(pk=request.POST['set'], is_active=True).first()
        overwrite = request.POST.get('conflict') == 'overwrite'
        if overwrite and not permissions.can_change(request.user):
            errors.append('Replacing values that were already entered needs permission to change entered consumables.')
        if not selected_ids:
            errors.append('Select at least one examination.')
        if not source and not chosen_set and not items and not errors:
            errors.append('Choose an examination or a saved set to copy from, or fill in at least one consumable row.')
        if errors:
            for e in errors:
                messages.error(request, e)
            ctx.update(rows=rows, preselected=set(selected_ids), posted_source=request.POST.get('source', ''),
                       posted_set=request.POST.get('set', ''), overwrite=overwrite)
            return render(request, 'inventory/bulk_apply.html', ctx)

        targets = [e for e in Examination.objects.filter(pk__in=selected_ids, is_active=True)
                   if not (source and e.pk == source.pk)]
        merged = {}
        if source:
            for it in items_from_examination(source):
                merged[it['consumable'].pk] = it
        if chosen_set:
            for it in items_from_set(chosen_set):
                merged[it['consumable'].pk] = it
        for it in items:  # typed rows win over copied ones
            merged[it['consumable'].pk] = it
        result = apply_items(targets, list(merged.values()), overwrite=overwrite)
        parts = [f'{result["added"]} added']
        if result['updated']:
            parts.append(f'{result["updated"]} updated')
        if result['skipped']:
            parts.append(f'{result["skipped"]} skipped (already on the examination)')
        messages.success(request, f'Applied to {len(targets)} examination(s): ' + ', '.join(parts) + '.')
        return redirect('exam_list')

    preselected = {int(i) for i in request.GET.getlist('exam') if i.isdigit()}
    ctx.update(rows=[{'consumable': '', 'quantity': '1', 'unit': '', 'required': '1', 'notes': ''}] * 2,
               preselected=preselected, posted_source='', posted_set=request.GET.get('set', ''), overwrite=False,
               start_modality=request.GET.get('modality', ''))
    return render(request, 'inventory/bulk_apply.html', ctx)


# ------------------------------------------------------------------ export
def export_excel(request):
    modality = Modality.objects.filter(slug=request.GET.get('modality', '')).first()
    if request.GET.get('download') and request.GET.get('layout') == 'exam':
        data = build_exam_view(
            modality=modality,
            include_inactive=request.GET.get('inactive') == '1',
            include_empty=request.GET.get('empty', '1') == '1')
        resp = HttpResponse(data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="{exam_view_filename(modality)}"'
        return resp
    if request.GET.get('download'):
        data = build_workbook(
            modality=modality,
            include_inactive=request.GET.get('inactive') == '1',
            include_empty=request.GET.get('empty', '1') == '1')
        resp = HttpResponse(data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="{export_filename(modality)}"'
        return resp
    return render(request, 'inventory/export.html', {
        'modalities': Modality.objects.all(), 'selected': modality.slug if modality else ''})


# ---------------------------------------------------------------- modalities
@edit_required
def modality_list(request):
    form = ModalityForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        m = Modality.create_with_defaults(form.cleaned_data['name'])
        m.color = form.cleaned_data['color']
        m.save(update_fields=['color'])
        messages.success(request, f'Modality "{m.name}" added.')
        return redirect('modality_list')
    return render(request, 'inventory/modality_list.html', {
        'form': form, 'modalities': Modality.objects.annotate(n=Count('examinations'))})


@edit_required
def modality_edit(request, pk):
    m = get_object_or_404(Modality, pk=pk)
    form = ModalityForm(request.POST or None, instance=m)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Modality updated.')
        return redirect('modality_list')
    return render(request, 'inventory/modality_form.html', {'form': form, 'modality': m})


@delete_required
@require_POST
def modality_delete(request, pk):
    m = get_object_or_404(Modality, pk=pk)
    try:
        name = m.name
        m.delete()
        messages.success(request, f'Modality "{name}" deleted.')
    except ProtectedError:
        messages.error(request, f'"{m.name}" still has examinations. Move or delete them first.')
    return redirect('modality_list')


# -------------------------------------------------------------- consumables
def consumable_list(request):
    qs = (Consumable.objects.select_related('category', 'default_unit')
          .prefetch_related('alternative_links__alternative')
          .annotate(n_exams=Count('examination_links', distinct=True)).order_by('name'))
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
    cat = request.GET.get('category', '')
    if cat == 'none':
        qs = qs.filter(category__isnull=True)
    elif cat:
        qs = qs.filter(category_id=cat)
    status = request.GET.get('status', 'active')
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'inventory/consumable_list.html', {
        'page': _paginate(request, qs), 'q': q, 'cat': cat, 'status': status,
        'categories': ConsumableCategory.objects.all(), 'querystring': params.urlencode(),
    })


def _typed_alternatives(request):
    names = []
    for raw in request.POST.getlist('alt_name'):
        names += split_names(raw)
    return names


def _alternatives_message(request, result):
    if result['created']:
        messages.info(request, 'New consumable(s) created for the alternatives you typed: '
                               + ', '.join(result['created']) + '.')
    if result['ignored']:
        messages.warning(request, 'Not used as alternatives (itself, or more than 10): '
                                  + ', '.join(result['ignored']) + '.')


@edit_required
def consumable_create(request):
    form = ConsumableForm(request.POST or None)
    alt_names = _typed_alternatives(request) if request.method == 'POST' else []
    if request.method == 'POST' and form.is_valid():
        c = form.save()
        _alternatives_message(request, set_alternatives(c, alt_names))
        messages.success(request, f'Consumable "{c.name}" added.')
        return redirect('consumable_list')
    return render(request, 'inventory/consumable_form.html',
                  {'form': form, 'title': 'Add consumable', 'alt_names_json': json.dumps(alt_names)})


@change_required
def consumable_edit(request, pk):
    c = get_object_or_404(Consumable, pk=pk)
    form = ConsumableForm(request.POST or None, instance=c)
    if request.method == 'POST':
        alt_names = _typed_alternatives(request)
    else:
        alt_names = [l.alternative.name for l in c.alternative_links.select_related('alternative')]
    if request.method == 'POST' and form.is_valid():
        form.save()
        _alternatives_message(request, set_alternatives(c, alt_names, replace=True))
        messages.success(request, 'Consumable updated.')
        return redirect('consumable_list')
    exams = c.examination_links.select_related('examination__modality')[:15]
    return render(request, 'inventory/consumable_form.html',
                  {'form': form, 'title': f'Edit {c.name}', 'consumable': c, 'links': exams,
                   'link_total': c.examination_links.count(), 'alt_names_json': json.dumps(alt_names)})


@change_required
@require_POST
def consumable_toggle(request, pk):
    c = get_object_or_404(Consumable, pk=pk)
    c.is_active = not c.is_active
    c.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, f'"{c.name}" {"activated" if c.is_active else "deactivated"}.')
    return _back(request, 'consumable_list')


@delete_required
@require_POST
def consumable_delete(request, pk):
    c = get_object_or_404(Consumable, pk=pk)
    if c.is_in_use:
        c.is_active = False
        c.save(update_fields=['is_active', 'updated_at'])
        messages.warning(request, f'"{c.name}" is in use (examinations, a saved set or an alternative), '
                                  'so it was deactivated instead of deleted.')
    else:
        name = c.name
        c.delete()
        messages.success(request, f'"{name}" deleted.')
    return redirect('consumable_list')


# ------------------------------------------------- categories and units setup
@edit_required
def category_list(request):
    form = CategoryForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Category added.')
        return redirect('category_list')
    return render(request, 'inventory/category_list.html', {
        'form': form, 'categories': ConsumableCategory.objects.annotate(n=Count('consumables'))})


@delete_required
@require_POST
def category_delete(request, pk):
    cat = get_object_or_404(ConsumableCategory, pk=pk)
    cat.delete()  # consumables keep existing, they become uncategorized
    messages.success(request, f'Category "{cat.name}" deleted. Its consumables are now uncategorized.')
    return redirect('category_list')


@edit_required
def unit_list(request):
    form = UnitForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Unit added.')
        return redirect('unit_list')
    return render(request, 'inventory/unit_list.html', {'form': form, 'units': Unit.objects.all()})


@edit_required
def unit_edit(request, pk):
    u = get_object_or_404(Unit, pk=pk)
    form = UnitForm(request.POST or None, instance=u)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Unit "{u.name}" updated.')
        return redirect('unit_list')
    return render(request, 'inventory/unit_form.html', {'form': form, 'unit': u})


@edit_required
@require_POST
def unit_toggle(request, pk):
    u = get_object_or_404(Unit, pk=pk)
    u.is_active = not u.is_active
    u.save(update_fields=['is_active'])
    messages.success(request, f'Unit "{u.name}" {"activated" if u.is_active else "deactivated"}.')
    return redirect('unit_list')


# ----------------------------------------------- consumables list (Excel) + auto-fill
def _suggestion(item, taken):
    return {
        'name': item.name, 'description': item.description, 'exists': item.name_key in taken, 'source': 'list',
        'category_id': item.category_id, 'category_name': item.category.name if item.category else '',
        'unit_id': item.default_unit_id, 'unit_name': item.default_unit.name if item.default_unit else '',
        'alternatives': split_names(item.alternatives),
    }


@edit_required
def consumable_suggest(request):
    """JSON suggestions while typing. Default: items from the imported Excel list (for the name field).
    kind=alt: existing consumables first, then list items (for the alternatives fields)."""
    q = ' '.join(request.GET.get('q', '').split())
    kind = request.GET.get('kind', '')
    total = CatalogItem.objects.count() + (Consumable.objects.count() if kind == 'alt' else 0)
    if len(q) < 2:
        return JsonResponse({'results': [], 'total': total})
    results = []
    if kind == 'alt':
        mine = list(Consumable.objects.select_related('category', 'default_unit').filter(name__istartswith=q)[:6])
        if len(mine) < 6:
            seen = {c.pk for c in mine}
            mine += list(Consumable.objects.select_related('category', 'default_unit')
                         .filter(name__icontains=q).exclude(pk__in=seen)[:6 - len(mine)])
        for c in mine:
            results.append({'name': c.name, 'description': c.description, 'exists': False, 'source': 'consumable',
                            'category_id': c.category_id, 'category_name': c.category.name if c.category else '',
                            'unit_id': c.default_unit_id, 'unit_name': c.default_unit.name, 'alternatives': []})
    limit = 8 - len(results)
    base = CatalogItem.objects.select_related('category', 'default_unit')
    found = list(base.filter(name__istartswith=q)[:limit]) if limit > 0 else []
    if 0 < limit and len(found) < limit:
        seen = {i.pk for i in found}
        found += list(base.filter(name__icontains=q).exclude(pk__in=seen)[:limit - len(found)])
    keys = [i.name_key for i in found]
    taken = set(Consumable.objects.annotate(k=Lower('name')).filter(k__in=keys).values_list('k', flat=True))
    have = {r['name'].lower() for r in results}
    for i in found:
        if kind == 'alt' and i.name_key in taken or i.name.lower() in have:
            continue                      # already offered as an existing consumable
        results.append(_suggestion(i, taken))
    return JsonResponse({'total': total, 'results': results})


@edit_required
def catalog_import(request):
    if request.method == 'POST':
        f = request.FILES.get('file')
        if not f:
            messages.error(request, 'Choose an Excel file (.xlsx) first.')
        elif not f.name.lower().endswith('.xlsx'):
            messages.error(request, 'Please upload an Excel .xlsx file (save older .xls files as .xlsx first).')
        elif f.size > 10 * 1024 * 1024:
            messages.error(request, 'That file is larger than 10 MB.')
        else:
            try:
                report = import_catalog(f, add_as_consumables=request.POST.get('add_all') == '1')
            except Exception:
                messages.error(request, 'That file could not be read. Check that it is a valid .xlsx workbook.')
            else:
                request.session['catalog_report'] = report
                messages.success(request, f'Consumables list updated: {report["new"]} new, {report["updated"]} changed.')
        return redirect('catalog_import')
    return render(request, 'inventory/catalog_import.html', {
        'report': request.session.pop('catalog_report', None),
        'total': CatalogItem.objects.count(),
        'sample': CatalogItem.objects.select_related('category', 'default_unit')[:12],
    })


@edit_required
def catalog_template(request):
    resp = HttpResponse(build_template(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = 'attachment; filename="consumables-list-template.xlsx"'
    return resp


# --------------------------------------- take a consumable off many examinations at once
@delete_required
def consumable_usage(request, pk):
    c = get_object_or_404(Consumable, pk=pk)
    rows = list(ExaminationConsumable.objects.filter(consumable=c)
                .values('examination__modality__slug', 'examination__modality__name',
                        'examination__modality__sort_order')
                .annotate(n=Count('pk')).order_by('examination__modality__sort_order'))
    return render(request, 'inventory/consumable_usage.html', {
        'consumable': c,
        'rows': [{'slug': r['examination__modality__slug'], 'name': r['examination__modality__name'], 'n': r['n']}
                 for r in rows],
        'total': sum(r['n'] for r in rows),
        'in_sets': c.set_items.count(),
        'as_alternative': c.alternative_for_links.count(),
    })


@delete_required
@require_POST
def consumable_remove_everywhere(request, pk):
    c = get_object_or_404(Consumable, pk=pk)
    links = ExaminationConsumable.objects.filter(consumable=c)
    scope = 'all examinations'
    slug = request.POST.get('modality', '')
    if slug:
        modality = get_object_or_404(Modality, slug=slug)
        links = links.filter(examination__modality=modality)
        scope = f'{modality.name} examinations'
    n = links.count()
    ExaminationConsumable.objects.filter(replaces=c, examination__in=links.values('examination')).update(replaces=None)
    links.delete()                      # only the pairings go; the consumable itself stays unless asked below
    messages.success(request, f'Removed "{c.name}" from {n} examination(s) ({scope}).')
    if request.POST.get('also_delete') == '1':
        if c.is_in_use:
            messages.warning(request, f'"{c.name}" was kept: it is still used by other examinations, a saved set '
                                      'or as an alternative.')
        else:
            name = c.name
            c.delete()
            messages.success(request, f'"{name}" deleted.')
            return redirect('consumable_list')
    elif not c.is_in_use:
        messages.info(request, f'Nothing uses "{c.name}" any more, so it can now be deleted from the consumables list.')
    return redirect('consumable_usage', pk=c.pk)
