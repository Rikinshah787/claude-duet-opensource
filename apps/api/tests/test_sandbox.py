"""Execution sandbox isolation."""
from __future__ import annotations

import pytest

from duet.core.sandbox import Sandbox, create_workspace


@pytest.fixture
def ws():
    w = create_workspace("test")
    yield w
    w.destroy()


class TestWorkspace:
    def test_path_traversal_is_rejected(self, ws):
        for attempt in ("../escape.txt", "../../etc/passwd", "a/../../../x"):
            with pytest.raises(ValueError):
                ws.write(attempt, "x")

    def test_normal_nested_writes_are_allowed(self, ws):
        ws.write("reports/q1/summary.txt", "fine")
        assert "reports/q1/summary.txt" in [f["name"] for f in ws.list_files()]

    def test_destroy_removes_everything(self):
        w = create_workspace("doomed")
        w.write("a.txt", "x")
        path = w.path
        w.destroy()
        assert not path.exists()


class TestExecution:
    def test_code_runs_and_returns_output(self, ws):
        result = Sandbox().run_python("print(6 * 7)", workspace=ws)
        assert result.ok
        assert "42" in result.stdout

    def test_created_files_are_reported_as_artifacts(self, ws):
        result = Sandbox().run_python(
            "open('out.txt','w').write('hello')", workspace=ws
        )
        assert any(a["name"] == "out.txt" for a in result.artifacts)

    def test_failure_is_captured_not_raised(self, ws):
        result = Sandbox().run_python("raise ValueError('boom')", workspace=ws)
        assert not result.ok
        assert result.exit_code != 0
        assert "boom" in result.stderr

    def test_runaway_code_is_killed(self, ws):
        result = Sandbox().run_python("while True: pass", workspace=ws, timeout_s=2)
        assert not result.ok
        assert "timed out" in result.error

    def test_output_is_capped(self, ws):
        result = Sandbox().run_python(
            "print('x' * 500_000)", workspace=ws, max_output=5_000
        )
        assert len(result.stdout) <= 5_100


class TestIsolation:
    """These are the tests that make the sandbox worth having."""

    @pytest.mark.parametrize(
        "var", ["DUET_SECRET_KEY", "DUET_DATABASE_URL", "AWS_SECRET_ACCESS_KEY"]
    )
    def test_host_secrets_are_not_visible(self, ws, var, monkeypatch):
        monkeypatch.setenv(var, "LEAKED-VALUE")
        result = Sandbox().run_python(
            f"import os; print(os.environ.get('{var}', 'absent'))", workspace=ws
        )
        assert "LEAKED-VALUE" not in result.stdout

    def test_planted_module_is_not_importable(self, ws):
        """`-I` matters: a task-written json.py must not shadow the stdlib."""
        ws.write("json.py", "raise SystemExit('planted module executed')")
        result = Sandbox().run_python(
            "import json; print(json.dumps({'ok': True}))", workspace=ws
        )
        assert result.ok
        assert '"ok": true' in result.stdout

    def test_cwd_is_the_workspace_not_the_repo(self, ws):
        result = Sandbox().run_python("import os; print(os.getcwd())", workspace=ws)
        assert str(ws.path.name) in result.stdout


class TestCapabilityReporting:
    def test_available_reports_honestly(self):
        caps = Sandbox().available()
        assert caps["subprocess"] is True
        assert isinstance(caps["docker"], bool)
        assert isinstance(caps["browser"], bool)
        assert "not a security boundary" in caps["note"]

    def test_browser_degrades_with_a_clear_message(self):
        """Playwright is optional; its absence must not look like a crash."""
        pytest.importorskip  # noqa: B018
        try:
            import playwright  # noqa: F401

            pytest.skip("playwright is installed; nothing to assert")
        except ImportError:
            result = Sandbox().browse("https://example.com")
            assert not result.ok
            assert "playwright" in result.error.lower()
