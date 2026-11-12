"""VPS provisioning tests (Task 2.11): host parsing, remote preflight,
bundle upload + remote deploy/verify/rollback via a FAKE ssh runner —
the suite never touches the network. Graceful-degrade rule: missing ssh /
failed auth degrades to a clean FAIL check, local paths untouched."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from teamops import vps, tools

ROOT = Path(__file__).resolve().parent.parent

PREFLIGHT_OK = (
    "Python 3.11.6\n---\n20971520\n---\n4096\n---\nWRITABLE\n---\n/usr/bin/tar"
)
PREFLIGHT_LOW_DISK = (
    "Python 3.11.6\n---\n102400\n---\n4096\n---\nWRITABLE\n---\n/usr/bin/tar"
)
PREFLIGHT_LOW_MEM = (
    "Python 3.11.6\n---\n20971520\n---\n128\n---\nWRITABLE\n---\n/usr/bin/tar"
)
PREFLIGHT_NO_PYTHON = (
    "sh: python3: not found\n---\n20971520\n---\n4096\n---\nWRITABLE\n---\n/usr/bin/tar"
)
PREFLIGHT_NO_TAR = (
    "Python 3.11.6\n---\n20971520\n---\n4096\n---\nWRITABLE\n---\nNO_TAR"
)

DEPLOY_OK = json.dumps({
    "success": True, "team": "sales",
    "checks": [{"name": "profile:teamops-sales-boss", "status": "PASS", "detail": "ok"}],
    "pass": 1, "fail": 0, "skip": 0, "green": True,
})


class FakeSshRunner:
    """Deterministic stand-in for `ssh host command` — no network.

    Inspects the command string, records stdin (bundle), and returns canned
    preflight/deploy/verify/rollback output.
    """

    def __init__(self, preflight_output: str = PREFLIGHT_OK,
                 deploy_json: str = DEPLOY_OK, auth_fail: bool = False):
        self.calls: list[tuple[str, bytes | None]] = []
        self.preflight_output = preflight_output
        self.deploy_json = deploy_json
        self.auth_fail = auth_fail
        self.verify_json = json.dumps({
            "success": True, "team": "sales", "checks": [], "pass": 0, "fail": 0,
            "skip": 0, "green": True,
        })
        self.rollback_json = json.dumps({
            "success": True, "team": "sales",
            "checks": [{"name": "rollback-snapshot", "status": "PASS", "detail": "removed"}],
            "pass": 1, "fail": 0, "skip": 0, "green": True,
        })

    def __call__(self, command, host, key=None, stdin_bytes=None, timeout=120):
        self.calls.append((command, stdin_bytes))
        if self.auth_fail:
            return 255, "Permission denied (publickey)."
        if "python3 --version" in command:
            return 0, self.preflight_output
        if "tar xzf -" in command:
            assert stdin_bytes is not None, "bundle should be piped over ssh stdin"
            return 0, "extracted"
        if "python3 - rollback" in command:
            return 0, self.rollback_json
        if "python3 - verify" in command:
            return 0, self.verify_json
        if "python3 - deploy" in command:
            return 0, self.deploy_json
        if "rm -rf" in command:
            return 0, ""
        return 0, "ok"


@pytest.fixture()
def fake_runner(monkeypatch):
    runner = FakeSshRunner()
    monkeypatch.setattr(vps.VpsDeploy, "run_ssh", staticmethod(runner))
    return runner


# -- host parsing -------------------------------------------------------------
def test_parse_host_user_host_port():
    assert vps.parse_host("alice@vps.example.com:2222") == ("alice", "vps.example.com", "2222")


def test_parse_host_bare_host_uses_current_user(monkeypatch):
    monkeypatch.setenv("USER", "dalton")
    assert vps.parse_host("vps.example.com") == ("dalton", "vps.example.com", None)


def test_parse_host_invalid():
    with pytest.raises(ValueError):
        vps.parse_host("alice@")
    with pytest.raises(ValueError):
        vps.parse_host("alice@bob@vps")


def test_host_target():
    assert vps.host_target("root@10.0.0.5") == "root@10.0.0.5"
    assert vps.host_target("root@10.0.0.5:2222") == "root@10.0.0.5:2222"


def test_transport_unavailable_without_ssh(monkeypatch):
    monkeypatch.setattr(vps.shutil, "which", lambda _: None)
    ok, reason = vps.transport_available("user@host")
    assert not ok
    assert "disabled" in reason


def test_transport_available_with_ssh():
    ok, reason = vps.transport_available("user@host")
    assert ok


# -- preflight ----------------------------------------------------------------
def test_preflight_green(fake_runner):
    vd = vps.VpsDeploy("sales", "user@vps")
    vd.preflight_remote()
    statuses = {c.name: c.status for c in vd.checks}
    assert statuses["vps-ssh"] == "PASS"
    assert statuses["remote-python3"] == "PASS"
    assert statuses["remote-disk"] == "PASS"
    assert statuses["remote-mem"] == "PASS"
    assert statuses["remote-home"] == "PASS"
    assert statuses["remote-tar"] == "PASS"
    assert all(c.status != "FAIL" for c in vd.checks)


def test_preflight_low_disk(fake_runner, monkeypatch):
    fake_runner.preflight_output = PREFLIGHT_LOW_DISK
    vd = vps.VpsDeploy("sales", "user@vps")
    vd.preflight_remote()
    by_name = {c.name: c.status for c in vd.checks}
    assert by_name["remote-disk"] == "FAIL"
    assert by_name["remote-python3"] == "PASS"


def test_preflight_low_mem(fake_runner, monkeypatch):
    fake_runner.preflight_output = PREFLIGHT_LOW_MEM
    vd = vps.VpsDeploy("sales", "user@vps")
    vd.preflight_remote()
    by_name = {c.name: c.status for c in vd.checks}
    assert by_name["remote-mem"] == "FAIL"


def test_preflight_no_python(fake_runner, monkeypatch):
    fake_runner.preflight_output = PREFLIGHT_NO_PYTHON
    vd = vps.VpsDeploy("sales", "user@vps")
    vd.preflight_remote()
    by_name = {c.name: c.status for c in vd.checks}
    assert by_name["remote-python3"] == "FAIL"


def test_preflight_no_tar(fake_runner, monkeypatch):
    fake_runner.preflight_output = PREFLIGHT_NO_TAR
    vd = vps.VpsDeploy("sales", "user@vps")
    vd.preflight_remote()
    by_name = {c.name: c.status for c in vd.checks}
    assert by_name["remote-tar"] == "FAIL"


def test_preflight_auth_failure_hint(fake_runner, monkeypatch):
    fake_runner.auth_fail = True
    vd = vps.VpsDeploy("sales", "user@vps")
    vd.preflight_remote()
    by_name = {c.name: c.status for c in vd.checks}
    assert by_name["vps-ssh"] == "FAIL"
    detail = next(c.detail for c in vd.checks if c.name == "vps-ssh")
    assert "ssh auth failed" in detail


# -- deploy flow --------------------------------------------------------------
def test_deploy_dry_run_makes_no_ssh_calls(fake_runner):
    vd = vps.VpsDeploy("sales", "user@vps")
    out = vd.deploy(dry_run=True)
    assert out["green"] is False or out["green"] is True  # shape check
    assert any(c["name"] == "vps-dry-run" and c["status"] == "PASS" for c in out["checks"])
    assert fake_runner.calls == []  # zero remote calls


def test_deploy_green_end_to_end(fake_runner):
    vd = vps.VpsDeploy("sales", "user@vps")
    out = vd.deploy(verify=True)
    assert out["green"] is True, [c for c in out["checks"]]
    commands = [c for c, _ in fake_runner.calls]
    assert any("python3 --version" in c for c in commands)          # preflight
    assert any("tar xzf -" in c for c in commands)                  # bundle upload
    assert any("python3 - deploy" in c for c in commands)           # remote deploy
    assert any("python3 - verify" in c for c in commands)           # remote smoke
    assert any("rm -rf" in c for c in commands)                     # cleanup
    assert "remote" in out and "deploy" in out["remote"] and "verify" in out["remote"]
    assert out.get("staging") is None  # cleaned up on success


def test_deploy_green_skips_verify_when_disabled(fake_runner):
    vd = vps.VpsDeploy("sales", "user@vps")
    out = vd.deploy(verify=False)
    assert out["green"] is True
    commands = [c for c, _ in fake_runner.calls]
    assert any("python3 - deploy" in c for c in commands)
    assert not any("python3 - verify" in c for c in commands)


def test_deploy_fails_on_preflight_and_never_uploads(fake_runner, monkeypatch):
    fake_runner.preflight_output = PREFLIGHT_NO_PYTHON
    vd = vps.VpsDeploy("sales", "user@vps")
    out = vd.deploy()
    assert out["green"] is False
    assert any(c["name"] == "remote-python3" and c["status"] == "FAIL" for c in out["checks"])
    assert not any("tar xzf" in c for c, _ in fake_runner.calls)


def test_deploy_reports_remote_failure(fake_runner, monkeypatch):
    fake_runner.deploy_json = json.dumps({
        "success": False, "error": "profile create failed (simulated)",
    })
    vd = vps.VpsDeploy("sales", "user@vps")
    out = vd.deploy()
    assert out["green"] is False
    assert any(c["name"] == "remote-deploy" and c["status"] == "FAIL" for c in out["checks"])
    # staging kept on failure for debugging
    assert out["staging"] is not None


def test_transport_disabled_returns_clean_fail(monkeypatch):
    monkeypatch.setattr(vps.shutil, "which", lambda _: None)
    vd = vps.VpsDeploy("sales", "user@vps")
    out = vd.deploy()
    assert out["green"] is False
    assert any(c["name"] == "vps-transport" and c["status"] == "FAIL" for c in out["checks"])
    detail = next(c["detail"] for c in out["checks"] if c["name"] == "vps-transport")
    assert "disabled" in detail


# -- verify / rollback flows --------------------------------------------------
def test_verify_remote(fake_runner):
    vd = vps.VpsDeploy("sales", "user@vps")
    out = vd.verify()
    assert out["green"] is True
    commands = [c for c, _ in fake_runner.calls]
    assert any("python3 - verify" in c for c in commands)


def test_rollback_remote(fake_runner):
    vd = vps.VpsDeploy("sales", "user@vps")
    out = vd.rollback()
    assert out["green"] is True
    commands = [c for c, _ in fake_runner.calls]
    assert any("python3 - rollback" in c for c in commands)


# -- bundle -------------------------------------------------------------------
def test_bundle_contains_package_and_driver():
    vd = vps.VpsDeploy("sales", "user@vps")
    bundle = vd._bundle()
    import io, tarfile
    tf = tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz")
    names = tf.getnames()
    assert "teamops/__init__.py" in names
    assert "teamops/blueprints/sales/team.yaml" in names
    assert "teamops/scripts/remote-driver.py" in names
    tf.close()


def test_driver_script_syntax():
    import subprocess
    subprocess.run(["python3", "-m", "py_compile", str(vps.DRIVER_SCRIPT)],
                   check=True)


# -- tool + CLI wiring --------------------------------------------------------
def test_tools_team_deploy_routes_to_vps(fake_runner):
    out = json.loads(tools.team_deploy({"name": "sales", "host": "user@vps"}))
    assert out["success"] is True
    assert out["mode"] == "vps"
    assert out["host"] == "user@vps"
    assert out["green"] is True


def test_tools_team_verify_and_rollback_host(fake_runner):
    v = json.loads(tools.team_verify({"name": "sales", "host": "user@vps"}))
    assert v["mode"] == "vps" and v["green"] is True
    r = json.loads(tools.team_rollback({"name": "sales", "host": "user@vps"}))
    assert r["mode"] == "vps" and r["green"] is True


def test_tools_team_deploy_no_host_still_local(tmp_path):
    """Without --host nothing about vps runs — local path stays local."""
    out = json.loads(tools.team_deploy({"name": "sales", "dry_run": True},
                                       home=str(tmp_path)))
    assert out["success"] is True
    assert out.get("mode") != "vps"


def test_cli_accepts_host_flags():
    """CLI parsing: --host/--ssh-key/--remote-home are accepted and forwarded.

    The vps summary is rendered as a Deploy Guard-style table by the CLI
    (matching local deploy UX), so we assert on the rendered table content.
    """
    from teamops.cli import main
    import subprocess, sys
    proc = subprocess.run(
        [sys.executable, "-m", "teamops.cli", "teams", "deploy", "sales",
         "--host", "root@1.2.3.4", "--ssh-key", "~/.ssh/id_ed25519",
         "--remote-home", "/opt/hermes", "--dry-run"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    table = proc.stdout
    assert "vps-dry-run" in table
    assert "PASS" in table
    assert "root@1.2.3.4" in table


def test_manifest_schemas_include_vps_params():
    """The new host/ssh_key/remote_home schema params don't break coherence."""
    from teamops import schemas
    for tool in ("team_deploy", "team_verify", "team_rollback"):
        props = schemas.SCHEMAS[tool]["schema"]["parameters"]["properties"]
        assert "host" in props
        assert "ssh_key" in props
        assert "remote_home" in props
        banned = {"home", "jobs_path", "db_path", "blueprints_root", "path"} & set(props)
        assert not banned, f"{tool} leaks path args: {banned}"


# -- review round 2 #1: shell hardening ---------------------------------------
def test_parse_host_refuses_dash_host():
    for bad in ("-oProxyCommand=evil", "-l root@host", "root@-oProxyCommand=x",
                "-vvv user@host"):
        with pytest.raises(ValueError):
            vps.parse_host(bad)


def test_vps_name_validated():
    for bad in ("bad name!", "Bad/Name", "", "..", "x" * 65, "a b"):
        with pytest.raises(ValueError, match="blueprint name"):
            vps.VpsDeploy(bad, "user@vps")
    vd = vps.VpsDeploy("sales-2", "user@vps")  # valid
    assert vd.name == "sales-2"


def test_tools_team_deploy_vps_rejects_bad_name():
    out = json.loads(tools.team_deploy({"name": "bad name!", "host": "user@vps"}))
    assert out["success"] is False
    assert "blueprint name" in out["error"]


def test_tools_team_deploy_vps_dash_host_degrades_cleanly():
    """A host starting with '-' cannot inject ssh options — clean disable."""
    out = json.loads(tools.team_deploy({"name": "sales", "host": "-oProxyCommand=evil"}))
    assert out["mode"] == "vps" and out["green"] is False
    assert any(c["name"] == "vps-transport" and c["status"] == "FAIL"
               for c in out["checks"])


def test_run_remote_quotes_remote_home(fake_runner):
    """shell-metachar values are shlex.quote()d, never interpolated raw."""
    vd = vps.VpsDeploy("sales", "user@vps",
                       remote_home="~/.hermes; touch /tmp/pwned")
    out = vd.deploy(verify=False)
    assert out["green"] is True
    cmds = [c for c, _ in fake_runner.calls]
    deploy_cmd = next(c for c in cmds if "python3 - deploy" in c)
    assert "HERMES_HOME='~/.hermes; touch /tmp/pwned'" in deploy_cmd
    assert "HERMES_HOME=~/.hermes;" not in deploy_cmd  # never interpolated raw


def test_default_run_ssh_uses_double_dash(monkeypatch):
    """`ssh -- <host> <cmd>` so option-injection via host names is impossible."""
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(vps.subprocess, "run", fake_run)
    vps.default_run_ssh("echo hi", "user@vps")
    assert captured["cmd"][-3:] == ["--", "user@vps", "echo hi"]


def test_driver_calls_handlers_with_dict_args():
    """Round 2 #8 + round 3 #1: the remote driver must call handlers the
    Hermes way (dict args for schema keys; CLI-only keys as kwargs)."""
    src = vps.DRIVER_SCRIPT.read_text()
    assert 'tools.team_deploy({"name": name}, home=home, verify=False)' in src
    assert 'tools.team_verify({"name": name}, home=home)' in src
    assert 'tools.team_rollback({"name": name}, home=home)' in src
