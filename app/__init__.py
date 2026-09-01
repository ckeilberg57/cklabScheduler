import logging
from datetime import timedelta

from flask import Flask

from app.config import Settings
from app.database import init_db
from app.pexip import PexipAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


def create_app():
    Settings.validate_web()

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    app.config["SECRET_KEY"] = Settings.SECRET_KEY
    app.config["SESSION_COOKIE_SECURE"] = Settings.SESSION_COOKIE_SECURE
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
    # No per-token CSRF expiry; token lives for the session lifetime.
    app.config["WTF_CSRF_TIME_LIMIT"] = None

    app.pexip = PexipAPI()

    from app.auth import init_auth
    init_auth(app)

    @app.context_processor
    def _inject_globals():
        return {
            "app_display_name": Settings.APP_DISPLAY_NAME,
            "local_auth_enabled": Settings.LOCAL_AUTH_ENABLED,
            "entra_auth_enabled": Settings.is_entra_auth_enabled(),
        }

    init_db()

    from app.routes.auth import auth_bp
    from app.routes.ui import ui_bp
    from app.routes.meetings import meetings_bp
    from app.routes.endpoints import endpoints_bp
    from app.routes.export import export_bp
    from app.routes.health import health_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(ui_bp)
    app.register_blueprint(meetings_bp)
    app.register_blueprint(endpoints_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(health_bp)

    @app.after_request
    def _set_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Permissions-Policy'] = (
            'camera=(), microphone=(), geolocation=(), payment=()'
        )
        if 'text/html' in response.content_type:
            response.headers['Content-Security-Policy'] = (
                "default-src 'none'; "
                "script-src 'self'; "
                "style-src 'self' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
            response.headers.setdefault(
                'Cache-Control', 'no-store, no-cache, must-revalidate'
            )
        return response

    return app
