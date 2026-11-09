import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.local_setup import bootstrap_admin, configure_local_env, upgrade_query_credentials


def _referenced_compose() -> str:
    return """services:
  mysql:
    environment:
      MYSQL_DATABASE: ${MYSQL_DATABASE:-query_agent}
      MYSQL_USER: ${MYSQL_USER:-query_app}
      MYSQL_PASSWORD: ${MYSQL_PASSWORD:?MYSQL_PASSWORD must be set}
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD must be set}
      MYSQL_READONLY_USER: ${MYSQL_QUERY_USER:-query_readonly}
      MYSQL_READONLY_PASSWORD: ${MYSQL_QUERY_PASSWORD:?MYSQL_QUERY_PASSWORD must be set}
"""


def test_configure_local_env_generates_distinct_database_secrets(tmp_path):
    template = tmp_path / ".env.example"
    target = tmp_path / ".env"
    compose = tmp_path / "compose.yml"
    template.write_text(
        "MYSQL_PASSWORD=\nMYSQL_ROOT_PASSWORD=\nMYSQL_QUERY_PASSWORD=\nJWT_SECRET=\n",
        encoding="utf-8",
    )
    compose.write_text(_referenced_compose(), encoding="utf-8")

    updates = configure_local_env(template=template, target=target, compose=compose)

    secrets = {
        updates["MYSQL_PASSWORD"],
        updates["MYSQL_ROOT_PASSWORD"],
        updates["MYSQL_QUERY_PASSWORD"],
    }
    assert len(secrets) == 3
    assert all(len(value) >= 24 for value in secrets)
    assert "${MYSQL_" not in target.read_text(encoding="utf-8")


def test_configure_local_env_aligns_mysql_and_enables_local_admin_auth(tmp_path):
    template = tmp_path / ".env.example"
    target = tmp_path / ".env"
    compose = tmp_path / "compose.yml"
    template.write_text(
        "MODEL_API_KEY=\nDATABASE_BACKEND=sqlite\nMYSQL_PASSWORD=\nMYSQL_ROOT_PASSWORD=\n"
        "MYSQL_QUERY_PASSWORD=\nJWT_SECRET=\n"
        "AUTH_ENABLED=false\nAGENT_CHECKPOINT_ENABLED=true\n",
        encoding="utf-8",
    )
    compose.write_text(_referenced_compose(), encoding="utf-8")

    updates = configure_local_env(template=template, target=target, compose=compose)
    content = target.read_text(encoding="utf-8")

    assert "DATABASE_BACKEND=mysql" in content
    assert f"MYSQL_PASSWORD={updates['MYSQL_PASSWORD']}" in content
    assert f"MYSQL_ROOT_PASSWORD={updates['MYSQL_ROOT_PASSWORD']}" in content
    assert "MYSQL_QUERY_USER=query_readonly" in content
    assert f"MYSQL_QUERY_PASSWORD={updates['MYSQL_QUERY_PASSWORD']}" in content
    assert "AGENT_CHECKPOINT_ENABLED=false" in content
    assert "AUTH_ENABLED=true" in content
    assert "JWT_SECRET=\n" not in content
    assert len(updates["JWT_SECRET"]) >= 32
    assert "LOCAL_ADMIN_USERNAME=admin" in content
    admin_password = updates["LOCAL_ADMIN_PASSWORD"]
    assert len(admin_password) >= 24
    assert admin_password == content.split("LOCAL_ADMIN_PASSWORD=", 1)[1].splitlines()[0]
    assert admin_password not in {
        updates["MYSQL_PASSWORD"],
        updates["MYSQL_ROOT_PASSWORD"],
        updates["MYSQL_QUERY_PASSWORD"],
    }


def test_configure_local_env_preserves_generated_admin_password_on_rerun(tmp_path):
    template = tmp_path / ".env.example"
    target = tmp_path / ".env"
    compose = tmp_path / "compose.yml"
    template.write_text("LOCAL_ADMIN_PASSWORD=\nMYSQL_PASSWORD=\n", encoding="utf-8")
    compose.write_text(_referenced_compose(), encoding="utf-8")

    first = configure_local_env(template=template, target=target, compose=compose)
    second = configure_local_env(template=template, target=target, compose=compose)

    assert second["LOCAL_ADMIN_PASSWORD"] == first["LOCAL_ADMIN_PASSWORD"]


def test_configure_local_env_preserves_unmanaged_existing_values(tmp_path):
    template = tmp_path / ".env.example"
    target = tmp_path / ".env"
    compose = tmp_path / "compose.yml"
    template.write_text("MODEL_API_KEY=\n", encoding="utf-8")
    target.write_text("MODEL_API_KEY=keep-me\nJWT_SECRET=existing-secret-value\n", encoding="utf-8")
    compose.write_text(_referenced_compose(), encoding="utf-8")

    configure_local_env(template=template, target=target, compose=compose)
    content = target.read_text(encoding="utf-8")

    assert "MODEL_API_KEY=keep-me" in content
    assert "JWT_SECRET=existing-secret-value" in content


def test_upgrade_query_credentials_preserves_existing_local_configuration(tmp_path):
    target = tmp_path / ".env"
    compose = tmp_path / "compose.yml"
    target.write_text(
        "MODEL_API_KEY=keep-model-key\nMYSQL_HOST=custom-host\nMYSQL_PASSWORD=custom-control-password\n",
        encoding="utf-8",
    )
    compose.write_text(_referenced_compose(), encoding="utf-8")

    updates = upgrade_query_credentials(target=target, compose=compose)
    content = target.read_text(encoding="utf-8")

    assert updates["MYSQL_QUERY_USER"] == "query_readonly"
    assert updates["MYSQL_ROOT_PASSWORD"] == "custom-control-password"
    assert len(updates["MYSQL_QUERY_PASSWORD"]) >= 24
    assert updates["MYSQL_QUERY_PASSWORD"] != "custom-control-password"
    assert "MODEL_API_KEY=keep-model-key" in content
    assert "MYSQL_HOST=custom-host" in content
    assert "MYSQL_PASSWORD=custom-control-password" in content
    assert "MYSQL_ROOT_PASSWORD=custom-control-password" in content
    assert "MYSQL_QUERY_USER=query_readonly" in content
    assert f"MYSQL_QUERY_PASSWORD={updates['MYSQL_QUERY_PASSWORD']}" in content


def test_upgrade_query_credentials_does_not_replace_existing_query_identity(tmp_path):
    target = tmp_path / ".env"
    compose = tmp_path / "compose.yml"
    original = (
        "MYSQL_PASSWORD=control-secret\nMYSQL_ROOT_PASSWORD=root-secret\n"
        "MYSQL_QUERY_USER=approved_reader\nMYSQL_QUERY_PASSWORD=vault-secret\n"
    )
    target.write_text(original, encoding="utf-8")
    compose.write_text(_referenced_compose(), encoding="utf-8")

    assert upgrade_query_credentials(target=target, compose=compose) == {}
    assert target.read_text(encoding="utf-8") == original


def test_bootstrap_admin_is_idempotent_and_does_not_reset_existing_password():
    hashed = []
    created = []
    updated = []

    result = bootstrap_admin(
        username="admin",
        password="local1234",
        get_user_fn=lambda username: {"id": 7, "username": username},
        create_user_fn=lambda *args: created.append(args) or 8,
        update_password_fn=lambda *args: updated.append(args),
        hash_password_fn=lambda value: hashed.append(value) or "hash",
    )

    assert result == {"status": "existing", "user_id": 7, "username": "admin"}
    assert hashed == []
    assert created == []
    assert updated == []


def test_bootstrap_admin_creates_or_explicitly_resets_account():
    created = []
    create_result = bootstrap_admin(
        username="admin",
        password="local1234",
        get_user_fn=lambda _username: None,
        create_user_fn=lambda *args: created.append(args) or 9,
        update_password_fn=lambda *_args: None,
        hash_password_fn=lambda _value: "hashed",
    )
    updated = []
    reset_result = bootstrap_admin(
        username="admin",
        password="newpass123",
        reset_existing=True,
        get_user_fn=lambda username: {"id": 9, "username": username},
        create_user_fn=lambda *_args: 10,
        update_password_fn=lambda *args: updated.append(args),
        hash_password_fn=lambda _value: "new-hash",
    )

    assert create_result["status"] == "created"
    assert created == [("admin", "hashed", "admin", "default")]
    assert reset_result["status"] == "reset"
    assert updated == [(9, "new-hash")]


def test_configure_local_env_refuses_to_overwrite_inputs(tmp_path):
    template = tmp_path / ".env.example"
    compose = tmp_path / "compose.yml"
    template.write_text("MYSQL_PASSWORD=\n", encoding="utf-8")
    compose.write_text("services: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must not overwrite"):
        configure_local_env(template=template, target=template, compose=compose)

    assert template.read_text(encoding="utf-8") == "MYSQL_PASSWORD=\n"


def test_configure_local_env_rejects_hardcoded_compose_passwords(tmp_path):
    template = tmp_path / ".env.example"
    target = tmp_path / ".env"
    compose = tmp_path / "compose.yml"
    template.write_text("MYSQL_PASSWORD=\n", encoding="utf-8")
    compose.write_text(
        """services:
  mysql:
    environment:
      MYSQL_DATABASE: query_agent
      MYSQL_USER: query_app
      MYSQL_PASSWORD: fixed-password
      MYSQL_ROOT_PASSWORD: fixed-password
      MYSQL_READONLY_USER: query_readonly
      MYSQL_READONLY_PASSWORD: fixed-password
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must require"):
        configure_local_env(template=template, target=target, compose=compose)


def test_direct_script_execution_resolves_app_package_without_pythonpath(tmp_path):
    template = tmp_path / ".env.example"
    compose = tmp_path / "compose.yml"
    output = tmp_path / ".env"
    template.write_text("MODEL_API_KEY=\nJWT_SECRET=\n", encoding="utf-8")
    compose.write_text(_referenced_compose(), encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts" / "local_setup.py"),
            "configure",
            "--template",
            str(template),
            "--output",
            str(output),
            "--compose",
            str(compose),
        ],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Local environment configured" in result.stdout
    assert "DATABASE_BACKEND=mysql" in output.read_text(encoding="utf-8")
