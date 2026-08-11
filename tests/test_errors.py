import httpx

from charlie.errors import ErrorClass, classify_exception


def test_connect_error_is_retryable_with_blueprint_wording():
    exc = httpx.ConnectError("Connection refused")
    error_class, message = classify_exception(exc)
    assert error_class == ErrorClass.RETRYABLE
    assert message == "I can't reach my reasoning service right now. Local functions are still available."


def test_connect_timeout_is_retryable():
    exc = httpx.ConnectTimeout("timed out")
    error_class, _ = classify_exception(exc)
    assert error_class == ErrorClass.RETRYABLE


def test_read_timeout_is_retryable():
    exc = httpx.ReadTimeout("timed out")
    error_class, _ = classify_exception(exc)
    assert error_class == ErrorClass.RETRYABLE


def test_permission_error_is_permission_required():
    error_class, message = classify_exception(PermissionError("denied"))
    assert error_class == ErrorClass.PERMISSION_REQUIRED
    assert "traceback" not in message.lower()
    assert "denied" not in message


def test_file_not_found_is_user_action_required():
    error_class, _ = classify_exception(FileNotFoundError("missing.txt"))
    assert error_class == ErrorClass.USER_ACTION_REQUIRED


def test_5xx_http_status_error_is_dependency_failure():
    request = httpx.Request("POST", "http://x/chat/completions")
    response = httpx.Response(503, request=request)
    exc = httpx.HTTPStatusError("server error", request=request, response=response)
    error_class, _ = classify_exception(exc)
    assert error_class == ErrorClass.DEPENDENCY_FAILURE


def test_401_http_status_error_is_permission_required():
    request = httpx.Request("POST", "http://x/chat/completions")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError("unauthorized", request=request, response=response)
    error_class, _ = classify_exception(exc)
    assert error_class == ErrorClass.PERMISSION_REQUIRED


def test_404_http_status_error_is_user_action_required():
    request = httpx.Request("GET", "http://x/y")
    response = httpx.Response(404, request=request)
    exc = httpx.HTTPStatusError("not found", request=request, response=response)
    error_class, _ = classify_exception(exc)
    assert error_class == ErrorClass.USER_ACTION_REQUIRED


def test_unknown_exception_defaults_to_recoverable():
    error_class, message = classify_exception(ValueError("weird internal state"))
    assert error_class == ErrorClass.RECOVERABLE
    assert "weird internal state" not in message


def test_no_message_ever_contains_a_traceback_marker():
    for exc in (httpx.ConnectError("x"), PermissionError("x"), FileNotFoundError("x"), RuntimeError("x")):
        _, message = classify_exception(exc)
        assert "Traceback" not in message
        assert "  File \"" not in message
