from pathlib import Path

from claw.paths import RuntimePaths


def test_source_runtime_paths_do_not_depend_on_working_directory(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("CLAW_HOME", raising=False)
    monkeypatch.chdir(tmp_path)

    paths = RuntimePaths.from_environment()

    assert paths.home == Path(__file__).resolve().parent.parent
    assert paths.sessions_dir == paths.home / "data" / "sessions"
    assert paths.tasks_dir == paths.home / "data" / "tasks"
    assert paths.approvals_dir == paths.home / "data" / "approvals"
    assert paths.downloads_dir == paths.home / "data" / "downloads"
    assert paths.skills_dir == paths.home / "skills"
    assert paths.logs_dir == paths.home / "logs"


def test_claw_home_and_prompt_overrides_are_explicit(tmp_path, monkeypatch) -> None:
    home = tmp_path / "runtime"
    system_prompt = tmp_path / "system.md"
    monkeypatch.setenv("CLAW_HOME", str(home))
    monkeypatch.setenv("CLAW_SYSTEM_PROMPT", str(system_prompt))

    paths = RuntimePaths.from_environment()

    assert paths.home == home.resolve()
    assert paths.env_file == home.resolve() / ".env"
    assert paths.skills_dir == home.resolve() / "skills"
    assert paths.system_prompt_file == system_prompt.resolve()
