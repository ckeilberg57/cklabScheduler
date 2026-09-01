from functools import wraps

from flask import jsonify, redirect, request, url_for
from flask_login import current_user


def login_required(f):
    """
    Require an authenticated session.
    API paths (/api/*) receive a 401 JSON response.
    All other paths are redirected to the login page.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Authentication required"}), 401
            next_url = request.url if request.method == "GET" else None
            return redirect(url_for("auth.login", next=next_url))
        return f(*args, **kwargs)
    return decorated


def role_required(role):
    """
    Require authentication and a minimum role.
    'administrator' satisfies any role requirement.
    'scheduler_user' only satisfies 'scheduler_user'.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Authentication required"}), 401
                return redirect(url_for("auth.login", next=request.url))
            if not current_user.has_role(role):
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Access denied"}), 403
                return redirect(url_for("auth.access_denied"))
            return f(*args, **kwargs)
        return decorated
    return decorator
