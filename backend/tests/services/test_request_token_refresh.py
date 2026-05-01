"""Phase 7a C6 - smoke tests for BackendCallbackServicer."""

import inspect


def test_servicer_class_exists():
    from app.services.broker_callback_server import BackendCallbackServicer

    assert hasattr(BackendCallbackServicer, "RequestTokenRefresh")
    assert inspect.iscoroutinefunction(BackendCallbackServicer.RequestTokenRefresh)


def test_start_backend_callback_server_callable():
    from app.services.broker_callback_server import start_backend_callback_server

    assert inspect.iscoroutinefunction(start_backend_callback_server)
