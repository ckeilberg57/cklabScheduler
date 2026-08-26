import logging

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
    app.pexip = PexipAPI()

    init_db()

    from app.routes.ui import ui_bp
    from app.routes.meetings import meetings_bp
    from app.routes.endpoints import endpoints_bp
    from app.routes.export import export_bp
    from app.routes.health import health_bp

    app.register_blueprint(ui_bp)
    app.register_blueprint(meetings_bp)
    app.register_blueprint(endpoints_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(health_bp)

    return app
