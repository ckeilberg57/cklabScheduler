import logging

logger = logging.getLogger(__name__)


def get_msal_app():
    """Return a configured MSAL ConfidentialClientApplication."""
    import msal
    from app.config import Settings

    authority = (
        Settings.ENTRA_AUTHORITY
        or f"https://login.microsoftonline.com/{Settings.ENTRA_TENANT_ID}"
    )
    return msal.ConfidentialClientApplication(
        Settings.ENTRA_CLIENT_ID,
        authority=authority,
        client_credential=Settings.ENTRA_CLIENT_SECRET,
    )


def initiate_auth_flow(redirect_uri=None):
    """
    Start the OAuth2 authorization code flow.
    Returns the flow dict that must be stored in the session.
    """
    from app.config import Settings

    scopes = ["openid", "profile", "email"]
    kwargs = {}
    if redirect_uri:
        kwargs["redirect_uri"] = redirect_uri
    elif Settings.ENTRA_REDIRECT_URI:
        kwargs["redirect_uri"] = Settings.ENTRA_REDIRECT_URI

    return get_msal_app().initiate_auth_code_flow(scopes=scopes, **kwargs)


def complete_auth_flow(auth_code_flow, callback_args):
    """
    Complete the authorization code flow.
    Returns (result_dict, error_message).
    result_dict contains 'id_token_claims' on success.
    Secrets and tokens are never logged.
    """
    try:
        result = get_msal_app().acquire_token_by_auth_code_flow(
            auth_code_flow, callback_args
        )
        if "error" in result:
            logger.warning(
                "Entra authentication error: %s", result.get("error")
            )
            return None, result.get("error_description") or result.get("error")
        return result, None
    except Exception:
        logger.error("Entra auth flow raised an exception", exc_info=False)
        return None, "Authentication failed. Please try again."


def extract_role_from_claims(id_token_claims):
    """
    Map Entra app roles to internal scheduler roles.
    Returns (role, error_message). role is None if access should be denied.
    """
    from app.config import Settings

    admin_role = Settings.ENTRA_REQUIRED_ADMIN_ROLE or "Scheduler.Administrator"
    user_role = Settings.ENTRA_REQUIRED_USER_ROLE or "Scheduler.User"

    roles = id_token_claims.get("roles", [])

    if admin_role in roles:
        return "administrator", None
    if user_role in roles:
        return "scheduler_user", None

    return (
        None,
        f"Access denied: your account does not have a required application role "
        f"({admin_role} or {user_role}).",
    )


def extract_identity_from_claims(id_token_claims):
    """Return (username, display_name) from token claims."""
    username = (
        id_token_claims.get("preferred_username")
        or id_token_claims.get("upn")
        or id_token_claims.get("email")
        or id_token_claims.get("sub")
    )
    display_name = (
        id_token_claims.get("name")
        or id_token_claims.get("displayName")
        or username
    )
    return username, display_name
