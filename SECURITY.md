# Security

This document describes the security model for SBALKC Scheduler and guidance for operators deploying it.

---

## Authentication

SBALKC Scheduler supports two authentication methods, independently enabled:

| Method | Description |
|---|---|
| **Local accounts** | SQLite-stored users with PBKDF2-SHA256 hashed passwords. Enabled by default. |
| **Microsoft Entra ID** | OIDC Authorization Code Flow via MSAL. Single-tenant. Optional. |

At least one method must be enabled at all times. The installer enforces this — it will not write a configuration with both methods disabled. The `Settings.validate_web()` startup check also rejects configurations where both are disabled.

**Roles:**

| Role | Access |
|---|---|
| `administrator` | Full access — satisfies any `has_role()` check |
| `scheduler_user` | Standard access — meeting creation and management |

Entra app roles map as follows: `Scheduler.Administrator` → `administrator`, `Scheduler.User` → `scheduler_user`. Users with no assigned role are denied access after authentication.

---

## Local account password security

- Passwords are hashed with **Werkzeug `generate_password_hash`** using the `pbkdf2:sha256:600000` scheme (PBKDF2-HMAC-SHA256 with 600,000 iterations).
- Minimum password length: **12 characters**, enforced at creation and reset.
- Passwords are **never stored in plaintext**, never logged, never displayed after entry, and never passed as command-line arguments.
- The installer collects the initial admin password using `read -rs` (no terminal echo) and passes it to Python via an environment variable — not via `argv`.
- The `manage_users` CLI uses `getpass.getpass()` (reads from `/dev/tty`, not echoed).

---

## Session security

Sessions are signed with `SECRET_KEY` (generated automatically by the installer via `openssl rand -hex 32`).

| Cookie attribute | Value |
|---|---|
| `Secure` | `true` in production (HTTPS only) |
| `HttpOnly` | `true` — JavaScript cannot access the session cookie |
| `SameSite` | `Lax` — allows OAuth redirect while protecting against CSRF |
| Lifetime | 8 hours from last request |

**Session fixation prevention:** `session.clear()` is called before `login_user()` on every login — this invalidates any session state accumulated before authentication.

**Open redirect protection:** The `?next=` parameter on the login URL is validated with `urlparse` — only same-origin paths are accepted; any URL with a netloc (host) component is rejected.

---

## CSRF protection

Flask-WTF (`CSRFProtect`) is applied globally. All state-changing requests must include a valid CSRF token.

- **HTML forms**: a hidden `{{ csrf_token() }}` field is included in all forms.
- **JSON API calls** (AJAX): the token is embedded in a `<meta name="csrf-token">` tag and sent as an `X-CSRFToken` request header.
- The CSRF token is tied to the session and expires with it.

---

## Credential storage

All runtime secrets are stored in `/etc/sbalkcScheduler/sbalkcScheduler.env`.

| Setting | Notes |
|---|---|
| `MGMT_PASS` | Pexip Management API password |
| `SECRET_KEY` | Flask session signing key (generated automatically; never prompted) |
| `O365_CLIENT_SECRET` | Azure AD client secret for Microsoft 365 integration |
| `ENTRA_CLIENT_SECRET` | Microsoft Entra client secret (when Entra auth is enabled) |

**File permissions:** `640 root:sbalkcscheduler` — readable only by root and the `sbalkcscheduler` service account.

**`SECRET_KEY` generation:** The installer generates this value with `openssl rand -hex 32` and writes it directly to the env file. It is never echoed to the terminal and is never prompted from the operator.

**Passwords at install time:** All secrets are collected via prompts that suppress terminal echo (`read -rs`). They are never written to shell history or log files.

---

## Audit logging

Authentication events are written to two destinations:

1. **Python logger** (`app.auth`): appears in `journalctl -u sbalkc-scheduler-web`.
2. **`auth_audit_log` SQLite table**: persists across restarts; queryable via `sqlite3 /var/lib/sbalkcScheduler/scheduler.db`.

Events logged: login success, login failure, logout, account disabled, invalid password, Entra auth success/failure, role assignment changes.

**What is never logged:** passwords, password hashes, client secrets, authorization codes, access tokens, ID tokens, refresh tokens, session cookies.

---

## Service account

The application runs as a dedicated, login-disabled service account (`sbalkcscheduler`, shell `/usr/sbin/nologin`). This account:
- owns the database directory (`/var/lib/sbalkcScheduler/`, mode 750)
- has read access to the application directory (`/opt/sbalkcScheduler/`, root:sbalkcscheduler 750)
- has read access to the configuration file (`/etc/sbalkcScheduler/sbalkcScheduler.env`, mode 640)

The web and worker processes run as this account under systemd.

---

## Network exposure

**Gunicorn listens on `127.0.0.1:5080` only.** It is not accessible from the network directly.

**Apache terminates TLS** and reverse-proxies `/sbalkcScheduler/` to Gunicorn. All external traffic goes through Apache, which enforces HTTPS.

The installer supports:
- Let's Encrypt (certbot) for public-facing deployments
- Self-signed certificates for internal/lab use
- Existing certificates (operator-provided paths)

---

## Pexip API account

The scheduler uses a Pexip Management API account to:
- List registered endpoints (status API, read-only)
- Dial and disconnect participants (command API, write)

**Recommended least-privilege configuration:**
- Create a dedicated Pexip administrator account for the scheduler
- Grant only the permissions required for registration status reads and participant dial/disconnect
- Do not use the Pexip default `admin` account
- Rotate the password on a schedule consistent with your organisation's policy

---

## Microsoft 365 credentials

When O365 integration is enabled, the scheduler uses an Azure AD application (client credentials flow) to send calendar invitations. Recommended configuration:
- Register a dedicated Azure AD application for the scheduler
- Grant only `Calendars.ReadWrite` and `Mail.Send` (or `Mail.Send.Shared`) on the specific mailbox
- Use a client secret with the shortest expiry your workflow permits, and rotate it before expiry
- Do not grant tenant-wide permissions beyond what is required

The `O365_CLIENT_SECRET` is stored in the env file with `640` permissions and is never logged or exposed in API responses.

---

## What must never be committed to version control

- Real `.env` files or any file containing live credentials
- The `sbalkcScheduler.env` configuration file from any deployment
- Private TLS key files (`*.key`, `*.pem`, `*.p12`)
- SQLite database files (`*.db`)
- Old monolithic `app.py` from the original build if it contains hardcoded credentials

The `.gitignore` in this repository excludes `.env`, `.env.*`, `*.db`, `*.key`, `*.pem`, and related patterns. These patterns are listed in `.gitignore` but **gitignore only prevents future tracking — it does not remove files that were already committed.** Before any push, verify with `git ls-files` that no sensitive files are tracked.

---

## Health endpoint

`GET /api/health` is a **public endpoint** (no authentication required). It returns operational status including an `authentication` field showing which methods are enabled:

```json
"authentication": { "local_enabled": true, "entra_enabled": false }
```

It does **not** expose:
- Pexip hostnames
- API credentials
- Database paths
- Secret keys
- Tenant IDs, client IDs, or any Entra configuration details

---

## OWASP Top 10:2025 Security Review

The following documents security controls mapped to the OWASP Top 10:2025 risk categories, based on a source-level review of commit `c98ed94`. This is not an OWASP compliance certification — OWASP Top 10 is an awareness framework. Claims below are limited to controls demonstrable from source code, configuration, automated tests, or documented deployment behavior.

| OWASP Category | Status | SBALKC Scheduler Controls | Validation / Evidence | Residual / Operational Considerations |
|---|---|---|---|---|
| **A01:2025 Broken Access Control** | COVERED | `@login_required` on all protected routes; `role_required()` for admin-only functions; 401 JSON for API paths, redirect for browser paths; session fixation prevention (`session.clear()` before `login_user()`); open redirect validation via `urlparse`; CSRF protection on all state-changing requests; integer meeting IDs with parameterized lookup | `tests/test_auth.py` (TestUnauthenticatedAPI, TestRoleEnforcement, TestCSRF); `tests/test_security.py` (TestOpenRedirectHelper, TestCSRFProtection) | No per-user meeting ownership model by design (shared scheduler). All authenticated users may read and manage all meetings. |
| **A02:2025 Security Misconfiguration** | COVERED | CSP with no `unsafe-inline` or `unsafe-eval`; `X-Frame-Options: DENY`; `X-Content-Type-Options: nosniff`; `Referrer-Policy: strict-origin-when-cross-origin`; `Permissions-Policy` denies camera/microphone/geolocation/payment; `SESSION_COOKIE_SECURE=true` default; `SESSION_COOKIE_HTTPONLY=True` hardcoded; `SESSION_COOKIE_SAMESITE=Lax`; Flask debug mode not set (off by default); Gunicorn binds `127.0.0.1:5080` only; HTTP→HTTPS redirect in Apache config; env file `640 root:sbalkcscheduler`; `SECRET_KEY` installer-generated; startup config validation (`Settings.validate_web()`) | `tests/test_security.py` (TestSecurityHeaders); `app/__init__.py` `_set_security_headers`; `deploy/sbalkcscheduler.conf`; `deploy/sbalkc-scheduler-web.service` | TLS certificate provisioning, OS-level hardening, and Apache `SSLProtocol`/cipher configuration are operator responsibilities. |
| **A03:2025 Software Supply Chain Failures** | PARTIALLY COVERED | All dependencies pinned in `requirements.txt`; `cryptography` pinned explicitly for security; Snyk Code static analysis reviewed at `c98ed94` (no active findings at time of review); release archive built via `git archive` from specific commit (no untracked file inclusion) | `requirements.txt`; Snyk Code scan at `c98ed94` | No Software Bill of Materials (SBOM) generated. No cryptographic artifact signing. SBOM and signing are potential future improvements. |
| **A04:2025 Cryptographic Failures** | COVERED | Passwords hashed with Werkzeug PBKDF2-SHA256, 600,000 iterations; `SECRET_KEY` generated by installer via `openssl rand -hex 32`, never prompted or echoed; `VERIFY_TLS=true` default for Pexip API; Entra/MSAL uses standard OIDC token validation; no plaintext passwords in source, logs, or API responses | `app/auth/local.py` (`hash_password`); `app/config.py` (`VERIFY_TLS`); `app/auth/entra.py` (MSAL); `deploy/install.sh` (key generation); `tests/test_auth.py` (TestPasswordHashing) | `VERIFY_TLS` can be set to `false` per-host (`REG_VERIFY_TLS`, `COMMAND_VERIFY_TLS`) — this is an operator-controlled risk. TLS certificate trust configuration for Pexip and Entra endpoints is an operator responsibility. |
| **A05:2025 Injection** | COVERED | All SQL uses parameterized queries (`?` placeholders via `sqlite3`); no dynamic SQL string concatenation; Jinja2 autoescaping enabled (Flask default); no `subprocess` or shell execution in production application code; production JavaScript contains no HTML sinks (`innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `DOMParser`, `createContextualFragment`, `srcdoc`); CSP `script-src 'self'` as defense-in-depth | `app/database.py`; `app/routes/meetings.py`; `app/static/app.js`; `tests/test_security.py` (TestErrorTextExtraction) | CSP is defense-in-depth, not the primary XSS control. Jinja2 autoescaping is the primary template injection defense. |
| **A06:2025 Insecure Design** | PARTIALLY COVERED | Credentials separated from source via env file; dedicated least-privilege service account (`sbalkcscheduler`, shell `/usr/sbin/nologin`); web and worker run as separate systemd units; Gunicorn behind Apache (not internet-facing); at least one auth method enforced at startup and by installer; local break-glass admin account design; role model enforced at route layer; Pexip credentials never exposed to browser | `app/config.py` (`validate_web()`); `deploy/install.sh`; `deploy/sbalkc-scheduler-web.service`; `deploy/sbalkc-scheduler-worker.service` | No formal threat model has been produced. Formal threat modeling is a potential future improvement. |
| **A07:2025 Authentication Failures** | COVERED | PBKDF2-SHA256 with 600,000 iterations; 12-character minimum password; identical error message for wrong username and wrong password (no user enumeration); session fixation prevention; `SESSION_COOKIE_SECURE=true`, `HttpOnly=True`, `SameSite=Lax`; CSRF token required on login form; `session.clear()` on logout; account disable functionality; Entra/MSAL OIDC Authorization Code Flow; open redirect protection on `?next=` | `app/auth/local.py`; `app/routes/auth.py`; `app/auth/entra.py`; `tests/test_auth.py` (232 tests; TestLocalLoginSuccess, TestOpenRedirect, TestCSRF, TestEntraAuth, TestAccountDisabled) | No account lockout on repeated failed attempts. Brute-force protection is an operational/infrastructure responsibility (Apache rate-limiting, fail2ban, or network controls). |
| **A08:2025 Software or Data Integrity Failures** | PARTIALLY COVERED | All dependencies pinned in `requirements.txt`; release archive built via `git archive` from a specific named commit; SHA-256 checksum generated for each release artifact; `upgrade.sh` detects and refuses legacy installation states | `requirements.txt`; release artifact `sbalkcScheduler-r10.tar.gz` (SHA-256: `cee76f9f45d72a4a942a720b908189636a83517e7d94b1dc21cb9086e3a7347c`) | No cryptographic artifact signing (GPG or similar). SHA-256 checksums provide integrity verification but not publisher authentication. Artifact signing and SBOM are potential future improvements. |
| **A09:2025 Security Logging and Alerting Failures** | PARTIALLY COVERED | Authentication events written to Python logger (→ `journald`, `journalctl -u sbalkc-scheduler-web`) and to `auth_audit_log` SQLite table; events include login success, login failure, logout, Entra auth success/failure, access denied, account disabled; sensitive values (passwords, tokens, secrets) are never logged; scheduler and Pexip errors logged via `sbalkc.worker` | `app/database.py` (`log_auth_event`); `app/routes/auth.py`; `worker.py`; `app/scheduler_jobs.py` | No automated security alerting mechanism is implemented. Log monitoring, alerting, and SIEM integration are operational responsibilities. |
| **A10:2025 Mishandling of Exceptional Conditions** | COVERED | All API routes catch exceptions and return structured JSON error responses (no stack trace disclosure to clients); Pexip API failures return `{"ok": false, "error": "..."}` with HTTP 500; database failures handled without credential or path disclosure; invalid input returns HTTP 400; worker/scheduler exceptions caught and logged, do not crash the process; JavaScript error rendering uses `textContent` (not `innerHTML`) via `getErrorText()`; health endpoint fails safely without exposing configuration | `app/routes/meetings.py`; `app/routes/endpoints.py`; `app/routes/health.py`; `app/static/app.js` (`getErrorText`, `showErrorToast`); `tests/test_security.py` (TestErrorTextExtraction) | Exception detail in API error messages (e.g., Pexip error strings) may reveal Pexip API behavior. This is considered acceptable for an authenticated internal tool. |

---

### Authentication and Authorization

SBALKC Scheduler implements two-layer access control: **authentication** (who you are) and **role-based authorization** (what you can do).

**Authentication** is enforced at the route layer via a custom `@login_required` decorator (`app/auth/decorators.py`) backed by Flask-Login. Unauthenticated API requests receive `401 {"ok": false, "error": "Authentication required"}`; unauthenticated browser requests are redirected to `/login`.

**Authorization** is enforced by a `@role_required(role)` decorator. The `has_role()` method on the `User` model implements a simple privilege hierarchy: `administrator` satisfies any role check; `scheduler_user` satisfies only `scheduler_user` checks.

Both local (PBKDF2-SHA256) and Microsoft Entra (OIDC/MSAL) authentication providers are supported and independently configurable. At least one provider must be enabled — this is enforced by both the installer and `Settings.validate_web()` at startup.

Entra users are mapped to roles through Azure AD app role assignments (`Scheduler.Administrator`, `Scheduler.User`). Users with no assigned app role are denied access after Entra authentication completes.

---

### CSRF Protection

Flask-WTF `CSRFProtect` is applied globally to the application (`app/auth/__init__.py`). This means every state-changing request must carry a valid CSRF token — there are no per-route exemptions.

- **HTML form submissions** include a hidden `{{ csrf_token() }}` field (rendered by `login.html`).
- **JSON API calls** from the frontend read the token from `<meta name="csrf-token">` (rendered by `index.html`) and send it as the `X-CSRFToken` request header.
- The CSRF token is bound to the Flask session and is invalidated when the session is cleared (e.g., on logout).

Automated tests (as of `c98ed94`) exercise real CSRF validation — `WTF_CSRF_ENABLED = False` is not set anywhere in the test suite. CSRF behavior is validated by:

| Test | Behavior verified |
|---|---|
| `TestCSRF::test_post_without_csrf_returns_400` | Missing token → 400 |
| `TestCSRF::test_post_with_invalid_csrf_token_returns_400` | Forged token → 400 |
| `TestCSRF::test_post_with_valid_csrf_token_passes_csrf_check` | Valid token → login succeeds |
| `TestCSRFProtection::test_create_meeting_without_csrf_returns_400` | Missing token on API → 400 |
| `TestCSRFProtection::test_delete_meeting_without_csrf_returns_400` | Missing token on API → 400 |
| `TestCSRFProtection::test_extend_meeting_without_csrf_returns_400` | Missing token on API → 400 |
| `TestCSRFProtection::test_create_meeting_with_invalid_csrf_returns_400` | Forged token on API → 400 |

---

### Browser Security Controls

All HTTP responses receive protective headers set unconditionally (`app/__init__.py` `_set_security_headers`):

| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), payment=()` |

HTML responses additionally receive:

| Header | Value |
|---|---|
| `Content-Security-Policy` | `default-src 'none'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'` |
| `Cache-Control` | `no-store, no-cache, must-revalidate` |

The CSP contains no `'unsafe-inline'` or `'unsafe-eval'` directives. All styles and scripts are loaded from `'self'` or explicitly whitelisted external origins (Google Fonts). JSON API responses do not receive CSP.

Production JavaScript (`app/static/app.js`) contains no HTML sink APIs (`innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `DOMParser`, `createContextualFragment`, `srcdoc`). Error messages are rendered using `textContent`.

---

### Credential and Secret Management

All runtime secrets are environment-variable based, loaded from `/etc/sbalkcScheduler/sbalkcScheduler.env` in production (mode `640 root:sbalkcscheduler`). The repository contains no operational credentials. The `.gitignore` excludes `.env`, `.env.*`, `*.db`, `*.key`, and `*.pem` patterns.

The `SECRET_KEY` is generated by the installer with `openssl rand -hex 32` and written directly to the env file — it is never prompted from the operator, never echoed to the terminal, and never stored in shell history.

Admin passwords are collected by the installer via `read -rs` (no echo) and passed to Python via environment variable (never via `argv`). The `manage_users` CLI uses `getpass.getpass()`.

Passwords are hashed with Werkzeug's `generate_password_hash` using `pbkdf2:sha256:600000` (PBKDF2-HMAC-SHA256, 600,000 iterations). Plaintext passwords are never stored, never logged, and never returned in API responses.

---

### Dependency and Supply Chain Security

Dependencies are declared in `requirements.txt` with pinned versions. The `cryptography` library is pinned explicitly (`cryptography==50.0.1`) due to its security sensitivity as a transitive dependency.

At the time of the `c98ed94` security review, Snyk Code reported no active findings. Static analysis is one layer of the security program and does not replace penetration testing or a formal security audit.

Release archives are built via `git archive` from a specific commit, preventing untracked local files from being included. SHA-256 checksums are generated for each release artifact for integrity verification.

---

### Logging and Exception Handling

Authentication events are written to both:
1. **Python logger** (`app.auth`) → `journald` → `journalctl -u sbalkc-scheduler-web`
2. **`auth_audit_log` SQLite table** (`/var/lib/sbalkcScheduler/scheduler.db`) — persists across restarts

Events logged: login success, login failure, logout, Entra auth success/failure, access denied, account disabled, role assignment changes.

**What is never logged:** passwords, password hashes, CSRF tokens, session cookies, Entra client secrets, OAuth authorization codes, access tokens, ID tokens, or refresh tokens.

Scheduler and Pexip errors are logged via the `sbalkc.worker` logger (→ `journalctl -u sbalkc-scheduler-worker`). Application exceptions return structured JSON without exposing stack traces or internal paths to clients.

No automated security alerting is implemented. Log aggregation, alerting, and SIEM integration are operational responsibilities.

---

### Deployment Security Responsibilities

The following security controls are the responsibility of the operator deploying SBALKC Scheduler:

- **TLS/HTTPS:** Certificate provisioning (Let's Encrypt, self-signed, or operator-provided), `SSLProtocol`, and cipher configuration in Apache are operator responsibilities.
- **OS and package patching:** Operating system, Apache, and system-level package patching are not managed by this application.
- **Pexip API account:** A dedicated, least-privilege Pexip administrator account should be created for the scheduler. Credential rotation is an operational responsibility.
- **Entra application configuration:** App registration, role assignment, redirect URIs, and client secret rotation are Azure/Entra operator responsibilities.
- **Network controls:** Firewall rules, Apache rate-limiting, and brute-force protection (e.g., fail2ban) are deployment responsibilities.
- **Database backup:** SQLite database backup and restoration procedures are operational responsibilities.
- **Log retention:** Log rotation, retention policy, and external log shipping are operational responsibilities.

---

### Security Testing

Security validation as of commit `c98ed94`:

| Test type | Scope | Result |
|---|---|---|
| Automated pytest (232 tests) | Authentication, authorization, CSRF (positive and negative), open redirect, security headers, CSP, XSS-safe rendering, credential audit, branding | 232 passed, 0 failed |
| Snyk Code static analysis | Python application code, dependency vulnerabilities | No active findings at `c98ed94` |
| Dependency review | `requirements.txt` version pinning | All versions pinned; `cryptography` explicitly pinned |
| Release artifact audit | Credential/secret scan of `sbalkcScheduler-r10.tar.gz` | No operational credentials found |
| Manual source review | OWASP Top 10:2025 mapping | Documented above |

No penetration test has been performed. No third-party security certification has been obtained.

---

### Known Limitations / Residual Risks

| Item | Notes |
|---|---|
| No account lockout | Repeated failed login attempts are logged but not automatically blocked. Brute-force protection requires network or infrastructure controls (Apache `mod_evasive`, fail2ban, etc.). |
| `VERIFY_TLS` operator-configurable | Setting `VERIFY_TLS=false` disables Pexip TLS certificate verification. This option exists for lab environments with self-signed certificates and must not be used in production without understanding the risk. |
| No formal threat model | No structured threat modeling exercise (e.g., STRIDE) has been performed. This is a potential future improvement. |
| No artifact signing | Release archives are integrity-verified by SHA-256 checksum but are not cryptographically signed. Publisher authentication requires a signing workflow (e.g., GPG), which is a potential future improvement. |
| No SBOM | No Software Bill of Materials is generated. SBOM generation is a potential future improvement. |
| No automated security alerting | Authentication failures and access-denied events are logged but do not trigger automated alerts. Alerting requires external log monitoring integration. |
| Pexip API error strings | Pexip API error descriptions are included in authenticated API responses (`{"ok": false, "error": "..."}`). This is accepted for an authenticated internal tool. |

OWASP Application Security Verification Standard (ASVS) 5.0 may be used as a future verification baseline for more granular application-security requirements.

---

## Reporting a security concern

If you identify a security vulnerability or have a concern about credential handling, contact the internal security team directly. Do not open a public issue.
