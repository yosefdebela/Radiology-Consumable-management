from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def qty(value):
    """3.00 -> 3, 2.50 -> 2.5 (no trailing zeros)."""
    if value in (None, ''):
        return ''
    try:
        d = Decimal(str(value))
    except Exception:
        return value                      # something typed that is not a number yet: show it as typed
    return format(d.quantize(Decimal('1')) if d == d.to_integral_value() else d.normalize(), 'f')
