import json

from scripts import proactive_analysis_runner as cli


def test_cli_requires_explicit_execute(capsys):
    assert cli.main([]) == 2
    assert capsys.readouterr().err.strip() == "E_PROACTIVE_EXECUTE_REQUIRED"


def test_cli_returns_stable_code_when_feature_is_disabled(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "get_proactive_analysis_runner",
        lambda: (_ for _ in ()).throw(RuntimeError("E_PROACTIVE_DISABLED:disabled")),
    )

    assert cli.main(["--execute"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": False, "error_code": "E_PROACTIVE_DISABLED"}
