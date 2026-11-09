import json

from scripts import verify_local_stack as stack_module
from scripts.verify_local_stack import verify_stack


def test_stack_smoke_passes_without_recording_response_bodies():
    calls = []

    def request(url, timeout):
        calls.append((url, timeout))
        if url.endswith("/health") or url.endswith("/ready"):
            return 200, "application/json"
        return 200, "text/html"

    report = verify_stack(request=request)

    assert report["blocking"] == []
    assert len(report["passed"]) == 3
    assert report["secrets_redacted"] is True
    assert len(calls) == 3


def test_stack_smoke_blocks_unready_api_and_frontend_errors():
    def request(url, timeout):
        if url.endswith("/health"):
            return 200, "application/json"
        if url.endswith("/ready"):
            return 503, "application/json"
        return 500, "text/plain"

    report = verify_stack(request=request)

    assert any("API readiness" in detail for detail in report["blocking"])
    assert any("前端入口" in detail for detail in report["blocking"])


def test_stack_smoke_warns_on_unexpected_frontend_content_type():
    report = verify_stack(
        request=lambda url, timeout: (200, "application/octet-stream")
        if url.endswith("5173")
        else (200, "application/json")
    )

    assert report["blocking"] == []
    assert any("内容类型" in detail for detail in report["warnings"])


def test_stack_smoke_redacts_transport_exception_text():
    def request(url, timeout):
        raise RuntimeError("password=do-not-leak")

    report = verify_stack(request=request)

    rendered = json.dumps(report, ensure_ascii=False)
    assert "do-not-leak" not in rendered
    assert len(report["blocking"]) == 3


def test_default_transport_disables_system_proxy_for_loopback(monkeypatch):
    captured = {}

    class Headers:
        @staticmethod
        def get_content_type():
            return "application/json"

    class Response:
        status = 200
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class Opener:
        @staticmethod
        def open(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return Response()

    def build_opener(handler):
        captured["proxies"] = handler.proxies
        return Opener()

    monkeypatch.setattr(stack_module.urllib.request, "build_opener", build_opener)

    assert stack_module._request("http://127.0.0.1:8000/health", 2.5) == (200, "application/json")
    assert captured == {
        "proxies": {},
        "url": "http://127.0.0.1:8000/health",
        "timeout": 2.5,
    }
