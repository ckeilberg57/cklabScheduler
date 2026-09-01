import logging
from urllib.parse import urlparse, urljoin, unquote

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_user, logout_user

from app.config import Settings
from app.database import log_auth_event

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


def _safe_redirect_url(target):
    """Return a validated same-origin path, or None.

    The return value is a canonical path extracted from the parsed URL —
    never the raw user-supplied string — so redirect() receives a
    server-constructed value rather than untrusted input verbatim.

    Rejected forms include:
      //evil.example (scheme-relative)
      \\evil.example / /\\evil.example (backslash variants)
      %2F%2Fevil.example (percent-encoded scheme-relative)
      %5C%5Cevil.example (percent-encoded backslash)
      javascript:, data:, vbscript: (dangerous schemes)
      https://evil.example (external domain)
    """
    if not target:
        return None
    try:
        decoded = unquote(str(target).strip())
    except Exception:
        return None
    if decoded.startswith("//") or decoded.startswith("\\"):
        return None
    if "\\" in decoded:
        return None
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, decoded))
    if test.scheme in ("http", "https") and test.netloc == ref.netloc:
        return test.path + ("?" + test.query if test.query else "")
    return None


# ── Local login ───────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("ui.index"))

    error = None
    prefill_username = ""

    if request.method == "GET":
        # Validate the next parameter now and store it server-side so that
        # redirect() never receives a raw user-supplied string.
        next_param = request.args.get("next", "")
        if next_param:
            safe_next = _safe_redirect_url(next_param)
            if safe_next:
                session["_login_next"] = safe_next

    if request.method == "POST":
        if not Settings.LOCAL_AUTH_ENABLED:
            error = "Local authentication is not enabled."
        else:
            from app.auth.local import verify_password
            from app.auth.models import get_user_row_by_username, update_last_login, get_user_by_id

            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            prefill_username = username

            # Use identical timing/message for wrong user and wrong password
            # to avoid username enumeration.
            row = get_user_row_by_username(username)
            ip = request.remote_addr

            if row and row["enabled"] and verify_password(row["password_hash"], password):
                user = get_user_by_id(row["id"])
                # Pop the validated destination BEFORE session.clear() wipes it.
                pending_next = session.pop("_login_next", None)
                session.clear()  # prevent session fixation
                login_user(user, remember=False)
                session.permanent = True
                update_last_login(user.id)
                log_auth_event(
                    "login_success",
                    username=username,
                    display_name=row["display_name"],
                    auth_provider="local",
                    ip_address=ip,
                    success=True,
                )
                return redirect(pending_next or url_for("ui.index"))
            else:
                log_auth_event(
                    "login_failure",
                    username=username,
                    auth_provider="local",
                    ip_address=ip,
                    success=False,
                    detail="invalid credentials" if not row else (
                        "account disabled" if not row["enabled"] else "wrong password"
                    ),
                )
                error = "Invalid username or password."
                logger.warning("Local login failed for username=%r ip=%s", username, ip)

    return render_template(
        "login.html",
        error=error,
        prefill_username=prefill_username,
    )


# ── Logout ────────────────────────────────────────────────────────────────────

@auth_bp.route("/logout")
def logout():
    username = getattr(current_user, "username", "unknown") if current_user.is_authenticated else "unknown"
    display_name = getattr(current_user, "display_name", None) if current_user.is_authenticated else None
    auth_provider = getattr(current_user, "auth_provider", "local") if current_user.is_authenticated else "local"

    log_auth_event(
        "logout",
        username=username,
        display_name=display_name,
        auth_provider=auth_provider,
        ip_address=request.remote_addr,
        success=True,
    )

    logout_user()
    session.clear()

    if auth_provider == "entra" and Settings.is_entra_auth_enabled():
        authority = (
            Settings.ENTRA_AUTHORITY
            or f"https://login.microsoftonline.com/{Settings.ENTRA_TENANT_ID}"
        )
        post_logout = Settings.ENTRA_POST_LOGOUT_REDIRECT_URI or url_for(
            "auth.login", _external=True
        )
        return redirect(
            f"{authority}/oauth2/v2.0/logout?post_logout_redirect_uri={post_logout}"
        )

    return redirect(url_for("auth.login"))


# ── Microsoft Entra ID ────────────────────────────────────────────────────────

@auth_bp.route("/auth/login_entra")
def entra_login():
    if not Settings.is_entra_auth_enabled():
        return redirect(url_for("auth.login"))

    from app.auth.entra import initiate_auth_flow

    redirect_uri = Settings.ENTRA_REDIRECT_URI or url_for(
        "auth.entra_callback", _external=True
    )
    flow = initiate_auth_flow(redirect_uri=redirect_uri)
    session["_entra_flow"] = flow
    return redirect(flow["auth_uri"])


@auth_bp.route("/auth/callback")
def entra_callback():
    if not Settings.is_entra_auth_enabled():
        return redirect(url_for("auth.login"))

    from app.auth.entra import (
        complete_auth_flow,
        extract_identity_from_claims,
        extract_role_from_claims,
    )
    from app.auth.models import upsert_entra_user

    flow = session.pop("_entra_flow", None)
    ip = request.remote_addr

    if not flow:
        logger.warning("Entra callback received without flow in session ip=%s", ip)
        return redirect(url_for("auth.login"))

    result, err = complete_auth_flow(flow, request.args)
    if err or not result:
        log_auth_event(
            "entra_error",
            auth_provider="entra",
            ip_address=ip,
            success=False,
            detail="auth flow error",
        )
        return render_template("login.html", error="Microsoft authentication failed. Please try again.")

    claims = result.get("id_token_claims", {})
    username, display_name = extract_identity_from_claims(claims)

    if not username:
        log_auth_event(
            "entra_error",
            auth_provider="entra",
            ip_address=ip,
            success=False,
            detail="missing identity claim",
        )
        return render_template("login.html", error="Could not determine identity from Microsoft token.")

    role, role_err = extract_role_from_claims(claims)
    if role_err:
        log_auth_event(
            "access_denied",
            username=username,
            auth_provider="entra",
            ip_address=ip,
            success=False,
            detail="missing required app role",
        )
        return render_template("access_denied.html", reason=role_err), 403

    user = upsert_entra_user(username, display_name or username, role)

    session.clear()
    login_user(user, remember=False)
    session.permanent = True

    log_auth_event(
        "login_success",
        username=username,
        display_name=display_name,
        auth_provider="entra",
        ip_address=ip,
        success=True,
    )
    logger.info("Entra login success username=%r role=%s", username, role)

    return redirect(url_for("ui.index"))


# ── Access denied ─────────────────────────────────────────────────────────────

@auth_bp.route("/access-denied")
def access_denied():
    log_auth_event(
        "access_denied",
        username=getattr(current_user, "username", None),
        auth_provider=getattr(current_user, "auth_provider", None),
        ip_address=request.remote_addr,
        success=False,
    )
    return render_template("access_denied.html"), 403
