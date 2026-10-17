"""Who may do what. Administrators can do everything; other people can always view, and may add/edit
or delete only if an administrator has switched that on for them."""
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from .models import UserProfile


def profile_of(user):
    try:
        return user.inventory_profile
    except UserProfile.DoesNotExist:
        return UserProfile.objects.create(user=user)


def is_admin(user):
    return user.is_authenticated and user.is_superuser


def can_edit(user):
    return user.is_authenticated and (user.is_superuser or profile_of(user).can_edit)


def can_change(user):
    """Change consumables that were already entered (admins always; others only if an admin allowed it)."""
    return user.is_authenticated and (user.is_superuser or (profile_of(user).can_change and profile_of(user).can_edit))


def can_delete(user):
    return user.is_authenticated and (user.is_superuser or (profile_of(user).can_delete and profile_of(user).can_edit))


def role_label(user):
    if is_admin(user):
        return 'Administrator'
    parts = [label for label, ok in (('add', can_edit(user)), ('change', can_change(user)),
                                      ('delete', can_delete(user))) if ok]
    return 'User: view' + (', ' + ', '.join(parts) if parts else ' only')


def _deny(request, message):
    if request.method == 'POST':
        messages.error(request, message)
        back = request.META.get('HTTP_REFERER')
        if back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
            return redirect(back)
        return redirect('dashboard')
    return render(request, 'inventory/forbidden.html', {'message': message}, status=403)


def _guard(check, message):
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not check(request.user):
                return _deny(request, message)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


edit_required = _guard(can_edit, 'Your account is view-only. Ask an administrator to allow adding data.')
change_required = _guard(can_change, 'Changing consumables that were already entered needs an administrator, '
                                     'or permission from one.')
delete_required = _guard(can_delete, 'Your account is not allowed to delete. Ask an administrator to allow it.')
admin_required = _guard(is_admin, 'Only administrators can do that.')
