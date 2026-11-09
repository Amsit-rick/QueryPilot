from app.repositories.mysql_task_repository import MySQLTaskRepository


def test_mysql_task_repository_exposes_task_contract():
    repository = MySQLTaskRepository()
    assert all(hasattr(repository, name) for name in (
        "create", "get", "update", "list", "add_step", "list_steps",
        "add_event", "list_events", "add_artifact", "list_artifacts",
    ))
