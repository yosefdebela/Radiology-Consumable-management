import math
from collections import defaultdict
from io import BytesIO

from django.db.models import Count, Q
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import ConsumableAlternative, Consumable, ConsumableSet, ConsumableSetItem, ExaminationConsumable, Examination, Modality

HEADER_FILL = PatternFill('solid', fgColor='12262B')
HEADER_FONT = Font(bold=True, color='FFFFFF')


def _sheet(wb, title, headers, rows, widths, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(headers)
    for c in ws[1]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(vertical='center', wrap_text=True)
    for row in rows:
        ws.append(row)
    # user-entered text is stored as plain text so a value like '=1+1' can never run as a formula
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, str):
                c.data_type = 's'
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'
    if rows:
        ws.auto_filter.ref = ws.dimensions
    return ws


def build_workbook(modality=None, include_inactive=False, include_empty=True):
    exams = Examination.objects.select_related('modality').order_by('modality__sort_order', 'name')
    mods = Modality.objects.all()
    if modality:
        exams = exams.filter(modality=modality)
        mods = mods.filter(pk=modality.pk)
    if not include_inactive:
        exams = exams.filter(is_active=True)

    wb = Workbook()

    # Sheet 1 - modalities
    ex_filter = Q() if include_inactive else Q(examinations__is_active=True)
    rows = [[m.name, m.examinations_total, m.examinations_with, m.examinations_total - m.examinations_with]
            for m in mods.annotate(
                examinations_total=Count('examinations', filter=ex_filter, distinct=True),
                examinations_with=Count('examinations', filter=ex_filter & Q(examinations__consumable_links__isnull=False),
                                        distinct=True))]
    _sheet(wb, 'Modalities', ['Modality', 'Examinations', 'With consumables', 'Without consumables'],
           rows, [30, 14, 18, 20], first=True)

    # Sheet 2 - examination consumables (one row per consumable, flat so it filters and pivots well)
    links = (ExaminationConsumable.objects.filter(examination__in=exams)
             .select_related('examination__modality', 'consumable__category', 'unit')
             .prefetch_related('consumable__alternative_links__alternative'))
    alt_text = lambda c: '; '.join(a.alternative.name for a in c.alternative_links.all())
    by_exam = {}
    for l in links:
        by_exam.setdefault(l.examination_id, []).append(l)
    rows = []
    for e in exams:
        ls = sorted(by_exam.get(e.pk, []), key=lambda l: (not l.is_required, l.consumable.name.lower()))
        if not ls:
            if include_empty:
                rows.append([e.modality.name, e.code, e.name, e.subcategory,
                             'Active' if e.is_active else 'Inactive', '', '', None, '', '', '', ''])
            continue
        for l in ls:
            rows.append([e.modality.name, e.code, e.name, e.subcategory,
                         'Active' if e.is_active else 'Inactive',
                         l.consumable.name, l.consumable.category.name if l.consumable.category else '',
                         (int(l.quantity) if l.quantity == l.quantity.to_integral_value() else float(l.quantity)), l.unit.name, 'Required' if l.is_required else 'Optional', l.notes, alt_text(l.consumable)])
    ws = _sheet(wb, 'Examination consumables',
                ['Modality', 'Code', 'Examination', 'Subcategory', 'Status', 'Consumable', 'Consumable category',
                 'Quantity', 'Unit', 'Required', 'Notes', 'Alternatives'],
                rows, [16, 10, 46, 26, 10, 30, 24, 10, 10, 11, 34, 40])
    for c in ws['H'][1:]:
        c.number_format = '0.##'

    # Sheet 3 - consumables catalogue (only those relevant to the export)
    used = ExaminationConsumable.objects.filter(examination__in=exams)
    cons = (Consumable.objects.select_related('category', 'default_unit')
            .prefetch_related('alternative_links__alternative')
            .annotate(n=Count('examination_links', filter=Q(examination_links__in=used)))
            .order_by('name'))
    if modality or not include_inactive:
        cons = cons.filter(Q(is_active=True) | Q(n__gt=0))
    rows = [[c.name, c.category.name if c.category else '', c.default_unit.name,
             'Active' if c.is_active else 'Inactive', c.n, alt_text(c)] for c in cons]
    _sheet(wb, 'Consumables', ['Consumable', 'Category', 'Default unit', 'Status', 'Used in examinations',
                               'Alternatives'], rows, [34, 26, 14, 10, 20, 40])

    # Sheet 4 - saved sets
    rows = [[i.consumable_set.name, i.consumable.name,
             int(i.quantity) if i.quantity == i.quantity.to_integral_value() else float(i.quantity),
             i.unit.name, 'Required' if i.is_required else 'Optional', i.notes]
            for i in ConsumableSetItem.objects.filter(consumable_set__is_active=True)
            .select_related('consumable_set', 'consumable', 'unit').order_by('consumable_set__name', 'consumable__name')]
    _sheet(wb, 'Saved sets', ['Set', 'Consumable', 'Quantity', 'Unit', 'Required', 'Notes'],
           rows, [30, 34, 10, 10, 11, 34])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_filename(modality=None):
    stamp = timezone.localtime().strftime('%Y-%m-%d')
    part = modality.slug if modality else 'all-modalities'
    return f'radiology-consumables_{part}_{stamp}.xlsx'



# ------------------------------------------------------------------ exam view
# One block per examination: modality, examination and machine code merged on the left, its required
# consumables in rows beside it, and the optional items shown as replacements next to the required item.
EV_HEADERS = ['Modality', 'Examination', 'Machine code', 'Required consumable', 'Qty', 'Unit',
              'Replacement if required item is unavailable']
EV_ORPHAN_HEADER = 'Other optional items (not linked to a required item)'
EV_WIDTHS = [16, 34, 13, 40, 7, 8, 78, 46]
_THIN = Side(style='thin', color='FF000000')
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_ARIAL, _ARIAL_B = Font(name='Arial', size=10), Font(name='Arial', size=10, bold=True)
_BLOCK_FILL = PatternFill('solid', fgColor='FFDDEBF7')


def _qty_text(q):
    return str(int(q)) if q == q.to_integral_value() else format(q.normalize(), 'f')


def _option_line(link):
    """'Syringe 20 ml (5 piece)' or, with a note, 'Gloves powder free (3 pair) - for allergic staff'."""
    text = f'{link.consumable.name} ({_qty_text(link.quantity)} {link.unit.name})'
    return f'{text} - {link.notes}' if link.notes else text


def _lines(text, chars):
    return sum(max(1, math.ceil(len(part) / chars)) for part in str(text).split('\n')) if text else 1


def exam_view_blocks(exams, include_empty=True):
    """Work out, per examination, its rows (required consumable + replacement lines) and leftover optional items."""
    links = (ExaminationConsumable.objects.filter(examination__in=exams)
             .select_related('consumable', 'unit', 'replaces'))
    by_exam = defaultdict(list)
    for l in links:
        by_exam[l.examination_id].append(l)
    alternatives = defaultdict(set)
    for primary_id, alt_id in ConsumableAlternative.objects.values_list('consumable_id', 'alternative_id'):
        alternatives[primary_id].add(alt_id)

    blocks = []
    for e in exams:
        mine = by_exam.get(e.pk, [])
        if not mine and not include_empty:
            continue
        required = sorted((l for l in mine if l.is_required), key=lambda l: l.consumable.name.lower())
        optional = sorted((l for l in mine if not l.is_required), key=lambda l: l.consumable.name.lower())
        replacements = {r.consumable_id: [] for r in required}
        leftovers = []
        for o in optional:
            if o.replaces_id in replacements:                       # set on the examination: "Replaces ..."
                targets = [o.replaces_id]
            else:                                                    # otherwise: listed as an alternative of it
                targets = [r.consumable_id for r in required if o.consumable_id in alternatives[r.consumable_id]]
            if targets:
                for t in targets:
                    replacements[t].append(o)
            else:
                leftovers.append(o)
        rows = [{'name': r.consumable.name, 'qty': r.quantity, 'unit': r.unit.name,
                 'replacements': '\n'.join(_option_line(o) for o in replacements[r.consumable_id]) or '-'}
                for r in required]
        if not rows:
            rows = [{'name': '' if mine else '(no consumables entered yet)', 'qty': None, 'unit': '', 'replacements': ''}]
        blocks.append({'exam': e, 'rows': rows, 'leftovers': '\n'.join(_option_line(o) for o in leftovers)})
    return blocks


def build_exam_view(modality=None, include_inactive=False, include_empty=True):
    exams = Examination.objects.select_related('modality')
    if modality:
        exams = exams.filter(modality=modality)
    if not include_inactive:
        exams = exams.filter(is_active=True)
    exams = sorted(exams, key=lambda e: (e.modality.sort_order, e.name.lower(), e.code))
    blocks = exam_view_blocks(exams, include_empty)
    show_left = any(b['leftovers'] for b in blocks)
    headers = EV_HEADERS + ([EV_ORPHAN_HEADER] if show_left else [])
    ncols = len(headers)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Exam Consumables'
    ws.append(headers)
    head_fill = PatternFill('solid', fgColor='FF1F4E78')
    for c in ws[1]:
        c.font, c.fill, c.border = Font(name='Arial', size=10, bold=True, color='FFFFFFFF'), head_fill, _BORDER
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 31.5

    r = 2
    for b in blocks:
        e, rows = b['exam'], b['rows']
        first, last = r, r + len(rows) - 1
        heights = []
        for i, row in enumerate(rows):
            n = max(_lines(row['name'], 38), _lines(row['replacements'], 84))
            if len(rows) == 1:
                n = max(n, _lines(e.name, 32), _lines(b['leftovers'], 50))
            heights.append(round((13.5 * n + 3) * 2) / 2)          # 16.5 for one line, 30 for two, 70.5 for five
            values = [e.modality.name if i == 0 else None, e.name if i == 0 else None, e.code if i == 0 else None,
                      row['name'], (int(row['qty']) if row['qty'] is not None and row['qty'] == row['qty'].to_integral_value()
                                    else (float(row['qty']) if row['qty'] is not None else None)),
                      row['unit'] or None, row['replacements'] or None]
            if show_left:
                values.append(b['leftovers'] or None if i == 0 else None)
            for col, v in enumerate(values, start=1):
                cell = ws.cell(row=r + i, column=col, value=v)
                if isinstance(v, str):
                    cell.data_type = 's'                    # text stays text, even if it starts with "="
                cell.border = _BORDER
                if col in (1, 2, 3) or (show_left and col == 8):
                    cell.font, cell.fill = _ARIAL_B if col != 8 else _ARIAL, _BLOCK_FILL
                    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                elif col == 4:
                    cell.font = _ARIAL_B if row['name'] and not row['name'].startswith('(') else Font(name='Arial', size=10, italic=True, color='FF5D7176')
                    cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
                elif col in (5, 6):
                    cell.font, cell.alignment = _ARIAL, Alignment(horizontal='center', vertical='center', wrap_text=True)
                else:
                    cell.font, cell.alignment = _ARIAL, Alignment(horizontal='general', vertical='center', wrap_text=True)
        # the left cells (and the leftovers cell) can be taller than their rows: give the last row the difference
        need = 13.5 * max(_lines(e.name, 32), _lines(b['leftovers'], 50)) + 3
        short = need - sum(heights)
        if short > 0:
            heights[-1] = round((heights[-1] + short) * 2) / 2
        for i, h in enumerate(heights):
            ws.row_dimensions[first + i].height = h
        if last > first:
            for col in ([1, 2, 3] + ([8] if show_left else [])):
                ws.merge_cells(start_row=first, start_column=col, end_row=last, end_column=col)
        r = last + 1

    for i, w in enumerate(EV_WIDTHS[:ncols], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = '1:1'
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def exam_view_filename(modality=None):
    stamp = timezone.localtime().strftime('%Y-%m-%d')
    return f'radiology-consumables_exam-view_{stamp}{"_" + modality.slug if modality else ""}.xlsx'
