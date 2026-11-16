import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api
from xianyu_core.guard import Guard

DEPLOY_SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "deploy.sh"


def test_health_reports_process_revision_not_new_files(monkeypatch, tmp_path, database_url):
    revision_file = tmp_path / "REVISION"
    revision_file.write_text("a" * 40 + "\n")
    monkeypatch.setattr(api, "REVISION_FILE", revision_file)
    app = api.create_app(
        database_url=database_url,
        accounts_dir=tmp_path / "accounts",
        images_dir=tmp_path / "images",
        web_dist=None,
        guard=Guard(),
    )
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"ok": True, "revision": "a" * 40}
        # 新文件已传完但旧进程尚未退出时，不能把旧进程报告成新版本。
        revision_file.write_text("b" * 40 + "\n")
        assert client.get("/api/health").json()["revision"] == "a" * 40


def test_health_without_revision_in_local_development(monkeypatch, tmp_path, database_url):
    monkeypatch.setattr(api, "REVISION_FILE", tmp_path / "missing")
    app = api.create_app(
        database_url=database_url,
        accounts_dir=tmp_path / "accounts",
        web_dist=None,
        guard=Guard(),
    )
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"ok": True}


@pytest.fixture
def remote_deploy(tmp_path):
    """在临时目录运行真实远端脚本；SSH、systemd、联网和数据库操作全部替换。"""
    revision = "a" * 40
    app, stage, commands = (tmp_path / name for name in ("app", "stage", "bin"))
    for directory in (app / ".venv" / "bin", app / "data", stage / "web" / "dist", commands):
        directory.mkdir(parents=True)
    (app / "data" / "keep").write_text("账号和图片不能删")
    (app / "old-file").write_text("旧版代码")
    (stage / "REVISION").write_text(revision + "\n")
    (stage / "web" / "dist" / "index.html").write_text("新版本")
    (app / ".venv" / "bin" / "python").symlink_to(sys.executable)
    trace = tmp_path / "trace"
    real_rsync = shutil.which("rsync")
    if not real_rsync:
        pytest.skip("部署脚本检查需要 rsync")
    mock = commands / "mock"
    mock.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "name = pathlib.Path(sys.argv[0]).name\n"
        "with open(os.environ['DEPLOY_TRACE'], 'a') as f: f.write(name + '\\n')\n"
        "if name == 'rsync': os.execv(os.environ['REAL_RSYNC'], [name, *sys.argv[1:]])\n"
        "if name == 'flock' and os.environ.get('LOCK_BUSY'): sys.exit(1)\n"
        "if name == 'uv' and os.environ.get('FAIL_SYNC'): sys.exit(1)\n"
        "if name == 'curl': print(json.dumps({'ok': True, 'revision': os.environ['HEALTH_REV']}))\n"
        "if name == 'runuser':\n"
        "    command = sys.argv[sys.argv.index('--') + 1:]\n"
        "    os.execvp(command[0], command)\n"
    )
    mock.chmod(0o755)
    for name in ("systemctl", "uv", "curl", "flock", "runuser", "id", "sleep", "journalctl"):
        (commands / name).symlink_to(mock)
    (app / ".venv" / "bin" / "alembic").symlink_to(mock)
    env_file = tmp_path / "xym.env"
    env_file.write_text("")
    remote = DEPLOY_SCRIPT.read_text().split("<<'REMOTE'\n", 1)[1].split("\nREMOTE\n", 1)[0]
    remote = remote.replace("/etc/xym.env", str(env_file)).replace(
        "/run/lock/xianyu-manager-deploy.lock", str(tmp_path / "deploy.lock")
    )

    def run(**overrides):
        env = (
            os.environ
            | {
                "PATH": f"{commands}:{os.environ['PATH']}",
                "DEPLOY_TRACE": str(trace),
                "REAL_RSYNC": real_rsync,
                "HEALTH_REV": revision,
            }
            | overrides
        )
        result = subprocess.run(
            ["bash", "-s", "--", str(app), str(stage), revision],
            input=remote,
            text=True,
            capture_output=True,
            env=env,
            timeout=15,
        )
        return result, trace.read_text().splitlines()

    return run, app, stage


def test_deploy_preserves_data_and_environment_and_checks_exact_revision(remote_deploy):
    run, app, _ = remote_deploy
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert "健康检查通过" in result.stdout
    assert (app / "data" / "keep").read_text() == "账号和图片不能删"
    assert (app / ".venv" / "bin" / "python").exists()
    assert not (app / "old-file").exists()
    assert (app / "web" / "dist" / "index.html").read_text() == "新版本"
    assert calls.index("uv") < calls.index("alembic") < calls.index("curl")


@pytest.mark.parametrize("failure", ["LOCK_BUSY", "FAIL_SYNC"])
def test_deploy_failures_do_not_report_success_or_run_later_steps(remote_deploy, failure):
    run, app, _ = remote_deploy
    result, calls = run(**{failure: "1"})
    assert result.returncode != 0
    assert "健康检查通过" not in result.stdout
    assert "alembic" not in calls and "curl" not in calls
    assert (app / "data" / "keep").exists()
    if failure == "LOCK_BUSY":
        assert "rsync" not in calls and (app / "old-file").exists()


def test_deploy_rejects_healthy_process_from_a_different_revision(remote_deploy):
    run, app, _ = remote_deploy
    result, calls = run(HEALTH_REV="b" * 40)
    assert result.returncode != 0
    assert "健康检查通过" not in result.stdout
    assert "运行的版本与目标不一致" in result.stderr
    assert calls.count("curl") == 30
    assert (app / "data" / "keep").exists()


def test_package_runs_outside_repo_and_archives_requested_commit(tmp_path):
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    (repo / "web").mkdir()
    script = repo / "deploy" / "deploy.sh"
    shutil.copy2(DEPLOY_SCRIPT, script)
    (repo / "web" / "package.json").write_text("{}")
    (repo / "version").write_text("指定的旧提交")

    def git(*args):
        return subprocess.check_output(
            [
                "git",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=DeployTest",
                "-c",
                "user.email=deploy@example.invalid",
                *args,
            ],
            cwd=repo,
            text=True,
        ).strip()

    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "previous")
    requested = git("rev-parse", "HEAD")
    (repo / "version").write_text("HEAD 里的新提交")
    git("commit", "-qam", "latest")
    commands = tmp_path / "bin"
    commands.mkdir()
    captured = tmp_path / "package.json"
    mock = commands / "mock"
    mock.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "name = pathlib.Path(sys.argv[0]).name\n"
        "if name == 'ssh' and 'mktemp' in sys.argv: print('/opt/xianyu-manager-release.ABC12345')\n"
        "if name == 'pnpm':\n"
        "    pathlib.Path('dist').mkdir(exist_ok=True)\n"
        "    pathlib.Path('dist/index.html').write_text('built')\n"
        "if name == 'rsync':\n"
        "    root = pathlib.Path(sys.argv[-2])\n"
        "    result = {'revision': (root/'REVISION').read_text().strip(),\n"
        "              'frontend': (root/'web/dist/index.html').read_text(),\n"
        "              'source': (root/'version').read_text(),\n"
        "              'git': (root/'.git').exists(), 'data': (root/'data').exists()}\n"
        "    pathlib.Path(os.environ['CAPTURE_PACKAGE']).write_text(json.dumps(result))\n"
        "if name == 'ssh' and 'bash' in sys.argv: sys.stdin.read()\n"
    )
    mock.chmod(0o755)
    for name in ("ssh", "rsync", "pnpm"):
        (commands / name).symlink_to(mock)
    result = subprocess.run(
        ["bash", str(script), "test-host", requested],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=15,
        env=os.environ
        | {"PATH": f"{commands}:{os.environ['PATH']}", "CAPTURE_PACKAGE": str(captured)},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(captured.read_text()) == {
        "revision": requested,
        "frontend": "built",
        "source": "指定的旧提交",
        "git": False,
        "data": False,
    }
