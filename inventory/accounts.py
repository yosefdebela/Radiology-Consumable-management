"""Sign-in, first-run setup, profile page and user management (administrators only)."""
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, login, password_validation, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST

from . import permissions
from .forms import StyledMixin
from .models import UserProfile

User = get_user_model()
MAX_FAILS, LOCK_SECONDS = 5, 15 * 60


# ------------------------------------------------------------------ sign in
@method_decorator(login_not_required, name='dispatch')
class ThrottledLoginView(LoginView):
    """Normal sign-in, but 5 wrong passwords in a row lock that name from that computer for 15 minutes."""
    template_name = 'registration/login.html'
    redirect_authenticated_user = True

    def _key(self):
        username = self.request.POST.get('username', '').strip().lower()
        return f'loginfail:{self.request.META.get("REMOTE_ADDR", "")}:{username}'

    def post(self, request, *args, **kwargs):
        if cache.get(self._key(), 0) >= MAX_FAILS:
            form = self.get_form_class()(request=request)
            return self.render_to_response(self.get_context_data(form=form, locked=True))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self._key()
        cache.set(key, cache.get(key, 0) + 1, LOCK_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._key())
        return super().form_valid(form)


# --------------------------------------------------------------- first run
class PasswordPairMixin:
    def clean(self):
        cd = super().clean()
        p1, p2 = cd.get('password1'), cd.get('password2')
        if p1 and p2:
            if p1 != p2:
                self.add_error('password2', 'The two passwords do not match.')
            else:
                try:
                    password_validation.validate_password(p1, User(username=cd.get('username', '')))
                except forms.ValidationError as e:
                    self.add_error('password1', e)
        return cd


class SetupForm(PasswordPairMixin, StyledMixin, forms.Form):
    username = forms.CharField(max_length=150)
    full_name = forms.CharField(max_length=150, required=False, label='Your name (optional)')
    password1 = forms.CharField(widget=forms.PasswordInput, label='Password')
    password2 = forms.CharField(widget=forms.PasswordInput, label='Repeat password')

    def clean_username(self):
        return self.cleaned_data['username'].strip()


@login_not_required
def setup(request):
    if User.objects.exists():
        return redirect('login')
    form = SetupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            if User.objects.exists():
                return redirect('login')
            cd = form.cleaned_data
            user = User.objects.create_superuser(cd['username'], password=cd['password1'], first_name=cd['full_name'])
            UserProfile.objects.create(user=user, can_edit=True, can_delete=True)
        login(request, user)
        messages.success(request, 'Administrator account created. You are signed in.')
        return redirect('dashboard')
    return render(request, 'registration/setup.html', {'form': form})


# ------------------------------------------------------------------ profile
def profile(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    for f in form.fields.values():
        f.widget.attrs['class'] = 'input'
    if request.method == 'POST' and form.is_valid():
        form.save()
        update_session_auth_hash(request, form.user)
        messages.success(request, 'Your password was changed.')
        return redirect('profile')
    return render(request, 'inventory/profile.html', {'form': form})


# --------------------------------------------------------- user management
ACCOUNT_TYPES = [('user', 'Standard user'), ('admin', 'Administrator')]


class UserBaseForm(StyledMixin, forms.Form):
    full_name = forms.CharField(max_length=150, required=False, label='Name')
    account_type = forms.ChoiceField(
        choices=ACCOUNT_TYPES, label='Account type',
        help_text='Administrators can do everything, including managing users.')
    can_edit = forms.BooleanField(required=False, label='Can add data',
                                  help_text='Standard users only. Off = view only.')
    can_change = forms.BooleanField(required=False, label='Can change consumables that were already entered',
                                    help_text='Standard users only. Quantities, units, notes, names and '
                                              'alternatives of saved entries. Needs "add data". '
                                              'Administrators can always do this.')
    can_delete = forms.BooleanField(required=False, label='Can delete data',
                                    help_text='Standard users only. Needs "add data".')

    def clean(self):
        cd = super().clean()
        if cd.get('account_type') == 'user' and not cd.get('can_edit'):
            for flag in ('can_change', 'can_delete'):
                if cd.get(flag):
                    self.add_error(flag, 'Allow adding data first; this needs it.')
        return cd


class UserCreateForm(PasswordPairMixin, UserBaseForm):
    username = forms.CharField(max_length=150)
    password1 = forms.CharField(widget=forms.PasswordInput, label='Password')
    password2 = forms.CharField(widget=forms.PasswordInput, label='Repeat password')
    field_order = ['username', 'full_name', 'password1', 'password2', 'account_type', 'can_edit', 'can_change',
                   'can_delete']

    def clean_username(self):
        name = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=name).exists():
            raise forms.ValidationError('That username is already taken.')
        return name


class UserEditForm(UserBaseForm):
    is_active = forms.BooleanField(required=False, label='Account is active',
                                   help_text='Untick to stop this person signing in. Their data is kept.')


def _active_admins():
    return User.objects.filter(is_superuser=True, is_active=True)


def _save_flags(user, cd):
    admin = cd['account_type'] == 'admin'
    user.first_name = cd.get('full_name', '')
    user.is_superuser = user.is_staff = admin
    user.save()
    prof = permissions.profile_of(user)
    prof.can_edit = True if admin else cd['can_edit']
    prof.can_change = True if admin else cd['can_change']
    prof.can_delete = True if admin else cd['can_delete']
    prof.save()


@permissions.admin_required
def user_list(request):
    users = User.objects.order_by('-is_superuser', 'username')
    for u in users:
        u.role = permissions.role_label(u)
    return render(request, 'inventory/user_list.html', {'users': users})


@permissions.admin_required
def user_create(request):
    form = UserCreateForm(request.POST or None, initial={'account_type': 'user'})
    if request.method == 'POST' and form.is_valid():
        cd = form.cleaned_data
        user = User.objects.create_user(cd['username'], password=cd['password1'])
        _save_flags(user, cd)
        messages.success(request, f'Account "{user.username}" created.')
        return redirect('user_list')
    return render(request, 'inventory/user_form.html', {'form': form, 'title': 'Add user'})


@permissions.admin_required
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    prof = permissions.profile_of(user)
    initial = {'full_name': user.first_name, 'account_type': 'admin' if user.is_superuser else 'user',
               'can_edit': prof.can_edit, 'can_change': prof.can_change,
               'can_delete': prof.can_delete, 'is_active': user.is_active}
    form = UserEditForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        cd = form.cleaned_data
        lose_admin = user.is_superuser and cd['account_type'] != 'admin'
        deactivate = user.is_active and not cd['is_active']
        if user == request.user and deactivate:
            form.add_error('is_active', 'You cannot deactivate your own account.')
        elif (lose_admin or deactivate) and user.is_superuser and not _active_admins().exclude(pk=user.pk).exists():
            form.add_error(None, 'There must always be at least one active administrator.')
        else:
            _save_flags(user, cd)
            user.is_active = cd['is_active']
            user.save(update_fields=['is_active'])
            messages.success(request, f'Account "{user.username}" updated.')
            return redirect('user_list')
    return render(request, 'inventory/user_form.html', {'form': form, 'title': f'Edit {user.username}', 'edited': user})


@permissions.admin_required
def user_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    form = SetPasswordForm(user, request.POST or None)
    for f in form.fields.values():
        f.widget.attrs['class'] = 'input'
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'New password set for "{user.username}".')
        return redirect('user_list')
    return render(request, 'inventory/user_password.html', {'form': form, 'edited': user})
