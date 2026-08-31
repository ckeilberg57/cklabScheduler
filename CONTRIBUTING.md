# Contributing

This document covers the local development setup and contribution expectations for the cklabScheduler project.

---

## Repository layout

```
app/                    Flask application package
  routes/               Blueprint route handlers
  static/               Frontend assets (app.js, styles.css)
  templates/            Jinja2 templates (index.html)
deploy/                 Installation and maintenance scripts
  install.sh            Interactive fresh-install
  upgrade.sh            Non-interactive upgrade
  uninstall.sh          Interactive removal
  verify_install.sh     Post-install automated checks
  TEST_PLAN.md          Manual validation procedure
  *.service             systemd unit files
  cklabscheduler.conf   Apache virtual host template
tests/                  Automated test suite (not deployed to server)
worker.py               Standalone scheduler process entry point
wsgi.py                 Gunicorn entry point
requirements.txt        Python dependencies (pinned versions)
.env.example            All supported configuration variables with placeholders
```

---

## Local development setup

### Prerequisites

- Python 3.12 (match the production version)
- macOS or Linux

### 1. Clone and create a virtual environment

```bash
git clone <repo-url>
cd cklabScheduler
python3.12 -m venv venv
source venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create a local `.env`

```bash
cp .env.example .env
```

Edit `.env` with the hostnames and credentials for your development Pexip environment. This file is gitignored — it must never be committed.

### 4. Run the development server

```bash
flask --app wsgi:application run --host 127.0.0.1 --port 5080
```

The application will be accessible at `http://127.0.0.1:5080`. Note that the production mount path is `/cklabScheduler/`; in development the app runs at `/`.

### 5. Run the scheduler worker (separate terminal)

```bash
source venv/bin/activate
python worker.py
```

---

## Running tests

```bash
source venv/bin/activate
python -m pytest tests/ -v
```

Tests use temporary SQLite databases and mock all Pexip API calls. They do not require a running Pexip system or a configured `.env`.

To run a single test file:

```bash
python -m pytest tests/test_endpoint_discovery.py -v
```

---

## Coding expectations

### General

- All changes to business logic must be accompanied by tests.
- Tests must not make real network calls. Mock `PexipAPI` and `_status_request` / `_client_request` using `unittest.mock`.
- Tests must not write to the filesystem outside of `tmp_path` fixtures.
- Do not add new dependencies without discussion. The dependency surface is intentionally small.

### Scheduler worker isolation

The scheduler worker (`worker.py`) is the **only** process that imports `app.scheduler_jobs`. The Flask web process must never import or run scheduling functions. This constraint exists because Gunicorn spawns multiple worker processes; if the scheduler ran inside Gunicorn, every worker process would trigger duplicate Pexip dial-outs.

**Do not add imports of `scheduler_jobs` to any file under `app/` or to `wsgi.py`.**

If you need to expose scheduler state to the web process, do it through the shared SQLite database.

### Settings class

`app/config.py` defines a `Settings` class with class-level `os.getenv()` calls evaluated at import time. When writing tests that need to override settings, use `unittest.mock.patch.object(Settings, "ATTR_NAME", value)`. Patches on Settings attributes take effect at the next read of the attribute — for context processors and route handlers that read settings at request time, apply the patch around the request, not just around `create_app()`.

### Deployment scripts

All shell scripts in `deploy/` use `set -euo pipefail`. Any change to a deployment script must pass `bash -n` before review:

```bash
bash -n deploy/install.sh
bash -n deploy/upgrade.sh
bash -n deploy/uninstall.sh
bash -n deploy/verify_install.sh
```

### Environment migration

When adding a new configuration variable:

1. Add it to `app/config.py` (`Settings` class) with a sensible default.
2. Add it to `.env.example` with a placeholder value and a comment.
3. Add a prompt to `deploy/install.sh` in the appropriate section.
4. Add a `_add_env_default` call to `deploy/upgrade.sh` step 10 so existing installations get the default without losing admin-set values.
5. Add the key name to the required-keys loop in `deploy/verify_install.sh`.
6. Add tests for the new setting.

---

## Pull request checklist

Before requesting review:

- [ ] `bash -n` passes on all deploy scripts
- [ ] `python -m pytest tests/ -v` passes with no failures
- [ ] No secrets, credentials, or internal hostnames in any committed file
- [ ] `.env.example` updated if new config variables were added
- [ ] `upgrade.sh` updated if new config variables require env-file migration
- [ ] No `scheduler_jobs` imported in web-process code
- [ ] New behaviour has accompanying tests
