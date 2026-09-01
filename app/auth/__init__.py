from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

login_manager = LoginManager()
csrf = CSRFProtect()


def init_auth(app):
    """Bind authentication extensions to the app and register callbacks."""
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        from app.auth.models import get_user_by_id
        return get_user_by_id(int(user_id))

    @login_manager.unauthorized_handler
    def handle_unauthorized():
        from flask import request, jsonify, redirect, url_for
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Authentication required"}), 401
        next_url = request.url if request.method == "GET" else None
        return redirect(url_for("auth.login", next=next_url))
