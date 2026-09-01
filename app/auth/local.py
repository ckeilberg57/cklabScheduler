from werkzeug.security import check_password_hash, generate_password_hash

MIN_PASSWORD_LENGTH = 12


def hash_password(password):
    """Return a Werkzeug PBKDF2-SHA256 hash of the given password."""
    return generate_password_hash(password, method="pbkdf2:sha256:600000")


def verify_password(password_hash, password):
    """Return True if the password matches the stored hash."""
    if not password_hash:
        return False
    return check_password_hash(password_hash, password)


def validate_password_strength(password):
    """Raise ValueError if the password does not meet minimum requirements."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )
