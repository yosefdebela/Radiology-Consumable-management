from django.contrib.auth import get_user_model
from django.shortcuts import redirect


class FirstRunSetupMiddleware:
    """Until the first administrator exists, every visit goes to the one-time setup page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (not request.user.is_authenticated and not request.path.startswith(('/setup/', '/static/'))
                and not get_user_model().objects.exists()):
            return redirect('setup')
        return self.get_response(request)
