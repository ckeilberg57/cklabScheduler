from flask import Blueprint, render_template

from app.auth.decorators import login_required

ui_bp = Blueprint("ui", __name__)


@ui_bp.route("/")
@login_required
def index():
    return render_template("index.html")
