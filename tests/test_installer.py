"""
Tests for Ubuntu version detection and dependency handling in deploy scripts.

These tests validate the installer and verification scripts without running them
on a real Ubuntu host.  Each test either:
  - Runs a self-contained bash snippet that replicates the script's logic with
    mocked environment variables (for version-gate tests), or
  - Parses the script source to verify correct package names and commands are used.
"""
import re
import subprocess
from pathlib import Path

import pytest

DEPLOY_DIR  = Path(__file__).parent.parent / "deploy"
INSTALL_SH  = DEPLOY_DIR / "install.sh"
VERIFY_SH   = DEPLOY_DIR / "verify_install.sh"
UPGRADE_SH  = DEPLOY_DIR / "upgrade.sh"
UNINSTALL_SH = DEPLOY_DIR / "uninstall.sh"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def _install_version_accepted(id_val: str, version_id_val: str) -> bool:
    """Return True if install.sh's version gate would accept this OS."""
    result = _bash(f"""
die() {{ exit 1; }}
ID="{id_val}"
VERSION_ID="{version_id_val}"
PRETTY_NAME="Test {version_id_val}"
[[ "${{ID:-}}" == "ubuntu" && ( "${{VERSION_ID:-}}" == "24.04" || "${{VERSION_ID:-}}" == "26.04" ) ]] \
    || die "rejected"
""")
    return result.returncode == 0


def _verify_platform_result(id_val: str, version_id_val: str) -> str:
    """Return 'ok' or 'fail' from verify_install.sh's platform check."""
    result = _bash(f"""
ok()   {{ echo "ok"; }}
fail() {{ echo "fail"; }}
ID="{id_val}"
VERSION_ID="{version_id_val}"
PRETTY_NAME="Test {version_id_val}"
if [[ "${{ID:-}}" == "ubuntu" && ( "${{VERSION_ID:-}}" == "24.04" || "${{VERSION_ID:-}}" == "26.04" ) ]]; then
    ok "Ubuntu ${{VERSION_ID}} LTS (${{PRETTY_NAME:-}})"
else
    fail "Expected Ubuntu 24.04 LTS or 26.04 LTS; detected: ${{PRETTY_NAME:-unknown}}"
fi
""")
    return result.stdout.strip()


def _verify_python_regex_matches(python_version_str: str) -> bool:
    """Return True if verify_install.sh's Python version regex matches the string."""
    result = _bash(f"""
PYVER="{python_version_str}"
[[ "${{PYVER}}" =~ Python\\ 3\\.(1[2-9]|[2-9][0-9]) ]] && echo "ok" || echo "fail"
""")
    return result.stdout.strip() == "ok"


# ── install.sh: Ubuntu version gate ──────────────────────────────────────────

class TestInstallVersionGate:
    def test_ubuntu_2404_accepted(self):
        assert _install_version_accepted("ubuntu", "24.04")

    def test_ubuntu_2604_accepted(self):
        assert _install_version_accepted("ubuntu", "26.04")

    def test_ubuntu_2204_rejected(self):
        assert not _install_version_accepted("ubuntu", "22.04")

    def test_ubuntu_2004_rejected(self):
        assert not _install_version_accepted("ubuntu", "20.04")

    def test_ubuntu_2504_rejected(self):
        # 25.04 is a non-LTS interim release — not supported
        assert not _install_version_accepted("ubuntu", "25.04")

    def test_debian_rejected(self):
        assert not _install_version_accepted("debian", "12")

    def test_empty_id_rejected(self):
        assert not _install_version_accepted("", "24.04")

    def test_empty_version_rejected(self):
        assert not _install_version_accepted("ubuntu", "")


# ── verify_install.sh: platform check ────────────────────────────────────────

class TestVerifyPlatformCheck:
    def test_ubuntu_2404_ok(self):
        assert _verify_platform_result("ubuntu", "24.04") == "ok"

    def test_ubuntu_2604_ok(self):
        assert _verify_platform_result("ubuntu", "26.04") == "ok"

    def test_ubuntu_2204_fail(self):
        assert _verify_platform_result("ubuntu", "22.04") == "fail"

    def test_ubuntu_2504_fail(self):
        assert _verify_platform_result("ubuntu", "25.04") == "fail"

    def test_debian_fail(self):
        assert _verify_platform_result("debian", "12") == "fail"

    def test_empty_id_fail(self):
        assert _verify_platform_result("", "24.04") == "fail"


# ── verify_install.sh: Python version regex ───────────────────────────────────

class TestVerifyPythonVersionRegex:
    def test_python_312_accepted(self):
        assert _verify_python_regex_matches("Python 3.12.3")

    def test_python_313_accepted(self):
        assert _verify_python_regex_matches("Python 3.13.0")

    def test_python_314_accepted(self):
        # Likely default on Ubuntu 26.04
        assert _verify_python_regex_matches("Python 3.14.0")

    def test_python_319_accepted(self):
        assert _verify_python_regex_matches("Python 3.19.0")

    def test_python_320_accepted(self):
        # Regex extended to 3.20+ so future releases don't produce spurious warnings
        assert _verify_python_regex_matches("Python 3.20.0")

    def test_python_311_rejected(self):
        assert not _verify_python_regex_matches("Python 3.11.0")

    def test_python_310_rejected(self):
        assert not _verify_python_regex_matches("Python 3.10.0")

    def test_python_2_rejected(self):
        assert not _verify_python_regex_matches("Python 2.7.18")


# ── install.sh source: package naming ────────────────────────────────────────

class TestInstallerPackageNaming:

    @pytest.fixture
    def install_src(self) -> str:
        return INSTALL_SH.read_text()

    @pytest.fixture
    def apt_install_lines(self, install_src) -> str:
        """Joined content of all apt-get install lines."""
        return " ".join(
            line for line in install_src.splitlines()
            if "apt-get install" in line
        )

    def test_no_versioned_python_package_in_apt_line(self, apt_install_lines):
        """Must not hard-code python3.12 or python3.12-venv in apt install."""
        assert "python3.12" not in apt_install_lines, (
            "Version-specific python3.12 package found in apt-get install line"
        )

    def test_generic_python3_in_apt_line(self, apt_install_lines):
        """Must install the distro-default python3 package."""
        assert re.search(r'\bpython3\b', apt_install_lines), (
            "Generic python3 package not found in apt-get install line"
        )

    def test_python3_venv_in_apt_line(self, apt_install_lines):
        """Must install python3-venv (generic, not python3.12-venv)."""
        assert "python3-venv" in apt_install_lines, (
            "python3-venv not found in apt-get install line"
        )
        assert "python3.12-venv" not in apt_install_lines, (
            "Version-specific python3.12-venv found; use the generic python3-venv"
        )

    def test_venv_creation_uses_python3(self, install_src):
        """Venv must be created with 'python3 -m venv', not 'python3.12 -m venv'."""
        venv_lines = [
            line for line in install_src.splitlines()
            if "-m venv" in line and "VENV" in line
        ]
        assert venv_lines, "No venv creation line found in install.sh"
        for line in venv_lines:
            assert "python3.12" not in line, (
                f"Version-specific python3.12 used in venv creation: {line!r}"
            )
            assert re.search(r'\bpython3\b', line), (
                f"Generic python3 not used in venv creation: {line!r}"
            )


# ── Timezone dependency handling ──────────────────────────────────────────────

class TestTimezoneDependencies:

    @pytest.fixture
    def apt_install_lines(self) -> str:
        src = INSTALL_SH.read_text()
        return " ".join(
            line for line in src.splitlines()
            if "apt-get install" in line
        )

    def test_system_tzdata_installed(self, apt_install_lines):
        """System tzdata package provides /usr/share/zoneinfo for Python zoneinfo."""
        assert re.search(r'\btzdata\b', apt_install_lines), (
            "tzdata not found in apt-get install line"
        )

    def test_no_python3_tzdata_in_apt_line(self, apt_install_lines):
        """python3-tzdata is unnecessary on Ubuntu LTS where system tzdata is present."""
        assert "python3-tzdata" not in apt_install_lines, (
            "python3-tzdata should not be in apt-get install; use system tzdata"
        )

    def test_no_tzdata_in_requirements_txt(self):
        """tzdata/python3-tzdata must not appear in pip requirements."""
        req = (Path(__file__).parent.parent / "requirements.txt").read_text().lower()
        assert "tzdata" not in req, (
            "tzdata-related entry found in requirements.txt; "
            "timezone data is provided by the OS tzdata package"
        )


# ── Shell syntax validation ───────────────────────────────────────────────────

@pytest.mark.parametrize("script_path", [
    INSTALL_SH,
    VERIFY_SH,
    UPGRADE_SH,
    UNINSTALL_SH,
], ids=lambda p: p.name)
def test_shell_syntax(script_path):
    """All deploy scripts must pass bash -n (syntax check)."""
    result = subprocess.run(
        ["bash", "-n", str(script_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n failed for {script_path.name}:\n{result.stderr}"
    )
