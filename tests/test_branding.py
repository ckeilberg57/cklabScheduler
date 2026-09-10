"""
Regression tests for the SBALKC debranding pass.

Verifies:
  - Default application display name is SBALKC Scheduler
  - Manager and Edge host examples in .env.example are neutral (*.example.com)
  - Installer prompts for Manager/Edge with neutral examples (not hardcoded)
  - .env.example carries the SBALKC Scheduler display-name default
  - No tracked shipped source contains CKLab-specific infrastructure hostnames:
      ck-collab-engtest.com, cklab-pexmgr, cklab-edges
"""
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).parent.parent
INSTALL_SH = REPO / "deploy" / "install.sh"
ENV_EXAMPLE = REPO / ".env.example"


# ── Application display name default ─────────────────────────────────────────

class TestAppDisplayNameDefault:
    def test_config_default_is_sbalkc_scheduler(self):
        """Settings.APP_DISPLAY_NAME falls back to 'SBALKC Scheduler'."""
        from app.config import Settings
        from unittest.mock import patch
        import os
        # Patch env so APP_DISPLAY_NAME is absent
        env_without = {k: v for k, v in os.environ.items() if k != "APP_DISPLAY_NAME"}
        with patch.dict(os.environ, env_without, clear=True):
            # Re-read the default by accessing the class attribute
            # (Settings reads os.getenv at class-definition time, so we check
            # the source value directly)
            import importlib, app.config as cfg
            default = cfg.Settings.APP_DISPLAY_NAME
        # The default is set at module level; verify the literal in source
        source = (REPO / "app" / "config.py").read_text()
        assert '"SBALKC Scheduler"' in source, \
            "app/config.py must use 'SBALKC Scheduler' as APP_DISPLAY_NAME default"

    def test_config_default_does_not_contain_cklab(self):
        """The APP_DISPLAY_NAME default must not contain 'CKlab' or 'CKlabs'."""
        source = (REPO / "app" / "config.py").read_text()
        # Find the APP_DISPLAY_NAME line
        for line in source.splitlines():
            if "APP_DISPLAY_NAME" in line and "os.getenv" in line:
                assert "CKlab" not in line and "CKlabs" not in line, \
                    f"APP_DISPLAY_NAME default must not mention CKlab/CKlabs: {line}"


# ── .env.example branding values ─────────────────────────────────────────────

class TestEnvExample:
    def _lines(self):
        return ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()

    def test_app_display_name_is_sbalkc_scheduler(self):
        """.env.example APP_DISPLAY_NAME must be 'SBALKC Scheduler'."""
        found = [l for l in self._lines() if l.startswith("APP_DISPLAY_NAME=")]
        assert found, ".env.example must contain an APP_DISPLAY_NAME= line"
        assert found[0] == "APP_DISPLAY_NAME=SBALKC Scheduler", \
            f"Expected 'APP_DISPLAY_NAME=SBALKC Scheduler', got: {found[0]}"

    def test_reg_status_host_is_neutral(self):
        """REG_STATUS_HOST example must not reference CKLab infrastructure."""
        found = [l for l in self._lines() if l.startswith("REG_STATUS_HOST=")]
        assert found, ".env.example must contain a REG_STATUS_HOST= line"
        assert "ck-collab-engtest" not in found[0].lower(), \
            f"REG_STATUS_HOST must not reference ck-collab-engtest: {found[0]}"
        assert "cklab" not in found[0].lower(), \
            f"REG_STATUS_HOST must not reference cklab: {found[0]}"

    def test_command_host_is_neutral(self):
        """COMMAND_HOST example must not reference CKLab infrastructure."""
        found = [l for l in self._lines() if l.startswith("COMMAND_HOST=")]
        assert found, ".env.example must contain a COMMAND_HOST= line"
        assert "ck-collab-engtest" not in found[0].lower(), \
            f"COMMAND_HOST must not reference ck-collab-engtest: {found[0]}"
        assert "cklab" not in found[0].lower(), \
            f"COMMAND_HOST must not reference cklab: {found[0]}"

    def test_no_lab_specific_hostnames_in_env_example(self):
        """No CKLab-specific infrastructure hostnames in .env.example."""
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        assert "ck-collab-engtest" not in text, \
            ".env.example must not contain ck-collab-engtest.com"
        assert "cklab-pexmgr" not in text, \
            ".env.example must not contain cklab-pexmgr"
        assert "cklab-edges" not in text, \
            ".env.example must not contain cklab-edges"


# ── Installer prompt defaults ─────────────────────────────────────────────────

class TestInstallerPrompts:
    def _sh(self):
        return INSTALL_SH.read_text(encoding="utf-8")

    def test_app_display_name_prompt_default_is_sbalkc(self):
        """Installer APP_DISPLAY_NAME prompt must default to 'SBALKC Scheduler'."""
        sh = self._sh()
        assert "SBALKC Scheduler" in sh, \
            "deploy/install.sh must use 'SBALKC Scheduler' as the display-name prompt default"
        # And must not default to CKlabs Scheduler
        for line in sh.splitlines():
            if "APP_DISPLAY_NAME" in line and "prompt_default" in line:
                assert "CKlabs" not in line and "CKlab" not in line, \
                    f"Installer APP_DISPLAY_NAME default must not mention CKlab: {line}"

    def test_installer_prompts_for_reg_status_host(self):
        """Installer must prompt for Pexip Manager hostname (not hardcode it)."""
        sh = self._sh()
        assert "REG_STATUS_HOST" in sh, \
            "Installer must collect REG_STATUS_HOST"
        # Manager value must come from a prompt, not a hardcoded assignment
        assert re.search(r'REG_STATUS_HOST=.*prompt', sh, re.IGNORECASE), \
            "REG_STATUS_HOST must be collected via a prompt function"

    def test_installer_prompts_for_command_host(self):
        """Installer must prompt for Pexip Edge hostname (not hardcode it)."""
        sh = self._sh()
        assert "COMMAND_HOST" in sh, \
            "Installer must collect COMMAND_HOST"
        assert re.search(r'COMMAND_HOST=.*prompt', sh, re.IGNORECASE), \
            "COMMAND_HOST must be collected via a prompt function"

    def test_installer_pexip_prompts_use_neutral_examples(self):
        """Pexip Manager/Edge prompt examples must not reference CKLab infra."""
        sh = self._sh()
        assert "ck-collab-engtest" not in sh, \
            "Installer must not contain ck-collab-engtest.com"
        assert "cklab-pexmgr" not in sh, \
            "Installer must not contain cklab-pexmgr"
        assert "cklab-edges" not in sh, \
            "Installer must not contain cklab-edges"

    def test_installer_allows_display_name_override(self):
        """Installer uses prompt_default for display name — operator can override."""
        sh = self._sh()
        # prompt_default accepts user input; empty reply falls back to default
        assert re.search(r'APP_DISPLAY_NAME.*prompt_default', sh), \
            "APP_DISPLAY_NAME must use prompt_default so operator can override"


# ── No CKLab-specific infrastructure hostnames in any tracked source ──────────

class TestNoLabSpecificHostnames:
    """
    Verify that no shipped source file contains CKLab-specific infrastructure
    hostnames.  Acceptable remaining 'cklab' occurrences are limited to:
      - GitHub repository URL references
      - Systemd service unit names (cklab-scheduler-*)
      - Runtime filesystem paths (/opt/cklabScheduler, /etc/cklabScheduler, etc.)
      - System user (cklabscheduler)
      - Apache config filenames (cklabscheduler.conf)
    None of those are the specific lab hostnames we're testing against.
    """

    def _tracked_text(self):
        """Return concatenated text of all tracked non-binary source files."""
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True, cwd=REPO
        )
        texts = []
        for path in result.stdout.splitlines():
            full = REPO / path
            if full.suffix in (".gz", ".zip", ".db", ".sqlite", ".png",
                               ".jpg", ".ico", ".woff", ".woff2"):
                continue
            try:
                texts.append(full.read_text(encoding="utf-8", errors="replace"))
            except (OSError, IsADirectoryError):
                pass
        return "\n".join(texts)

    def test_no_ck_collab_engtest_in_source(self):
        """ck-collab-engtest.com must not appear in any tracked source file."""
        combined = self._tracked_text()
        assert "ck-collab-engtest" not in combined, \
            "Found 'ck-collab-engtest' in tracked source — remove all lab-specific hostnames"

    def test_no_cklab_pexmgr_in_source(self):
        """cklab-pexmgr must not appear in any tracked source file."""
        combined = self._tracked_text()
        assert "cklab-pexmgr" not in combined, \
            "Found 'cklab-pexmgr' in tracked source — remove lab-specific Pexip hostnames"

    def test_no_cklab_edges_in_source(self):
        """cklab-edges must not appear in any tracked source file."""
        combined = self._tracked_text()
        assert "cklab-edges" not in combined, \
            "Found 'cklab-edges' in tracked source — remove lab-specific Pexip hostnames"
