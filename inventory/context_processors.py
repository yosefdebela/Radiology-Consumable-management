from django.db.models import Count, Q

from . import permissions
from .models import Modality


def sidebar(request):
    modalities = Modality.objects.annotate(
        active_count=Count('examinations', filter=Q(examinations__is_active=True)))
    return {'nav_modalities': modalities}


def access(request):
    u = request.user
    if not u.is_authenticated:
        return {'perm': {'can_edit': False, 'can_change': False, 'can_delete': False, 'is_admin': False, 'role': ''}}
    return {'perm': {'can_edit': permissions.can_edit(u), 'can_change': permissions.can_change(u), 'can_delete': permissions.can_delete(u),
                     'is_admin': permissions.is_admin(u), 'role': permissions.role_label(u)}}
