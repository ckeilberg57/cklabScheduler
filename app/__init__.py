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

    return app
