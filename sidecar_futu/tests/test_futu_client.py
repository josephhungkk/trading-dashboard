"""B4 — _init_attempt + _init_loop with real SysConfig + OpenSecTradeContext + tempfile."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from sidecar_futu.futu_client import FutuClient, FutuCreds


def _make_rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _make_creds() -> FutuCreds:
    return FutuCreds(
        unlock_pwd_md5="0" * 32,
        rsa_priv_pem=_make_rsa_pem(),
        opend_host="127.0.0.1",
        opend_port=11111,
        connection_id="conn",
    )


@pytest.mark.asyncio
async def test_init_attempt_writes_rsa_tempfile_and_marks_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: SysConfig encrypted + RSA file set + ctx.unlock_trade returns RET_OK."""
    client = FutuClient()
    client._creds = _make_creds()

    sys_config_calls: list[tuple[str, Any]] = []
    fake_sys_config = MagicMock()
    fake_sys_config.enable_proto_encrypt.side_effect = lambda v: sys_config_calls.append(
        ("enable_proto_encrypt", v)
    )
    fake_sys_config.set_init_rsa_file.side_effect = lambda p: sys_config_calls.append(
        ("set_init_rsa_file", p)
    )
    monkeypatch.setattr("sidecar_futu.futu_client.SysConfig", fake_sys_config)

    fake_ctx = MagicMock()
    fake_ctx.unlock_trade.return_value = (0, "OK")
    init_kwargs: dict[str, Any] = {}

    def fake_factory(**kwargs: Any) -> Any:
        init_kwargs.update(kwargs)
        return fake_ctx

    monkeypatch.setattr("sidecar_futu.futu_client.OpenSecTradeContext", fake_factory)

    await client._init_attempt()

    assert client.gateway_connected is True
    assert sys_config_calls[0] == ("enable_proto_encrypt", True)
    assert sys_config_calls[1][0] == "set_init_rsa_file"
    rsa_path = sys_config_calls[1][1]
    assert client._rsa_tempfile_path is not None
    assert str(client._rsa_tempfile_path) == rsa_path
    assert init_kwargs["is_encrypt"] is True
    assert init_kwargs["host"] == "127.0.0.1"
    assert init_kwargs["port"] == 11111
    fake_ctx.unlock_trade.assert_called_once_with(password_md5="0" * 32)
    mode = os.stat(rsa_path).st_mode & 0o777
    assert mode == 0o600

    client._cleanup_rsa_tempfile()
    assert client._rsa_tempfile_path is None
    assert not Path(rsa_path).exists()  # noqa: ASYNC240


@pytest.mark.asyncio
async def test_init_attempt_raises_on_unlock_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """unlock_trade returning a non-OK code closes the ctx and raises."""
    client = FutuClient()
    client._creds = _make_creds()

    monkeypatch.setattr("sidecar_futu.futu_client.SysConfig", MagicMock())

    fake_ctx = MagicMock()
    fake_ctx.unlock_trade.return_value = (-1, "bad password")
    monkeypatch.setattr(
        "sidecar_futu.futu_client.OpenSecTradeContext", lambda **_: fake_ctx
    )

    with pytest.raises(RuntimeError, match="unlock_trade failed: bad password"):
        await client._init_attempt()

    fake_ctx.close.assert_called_once()
    assert client.gateway_connected is False
    client._cleanup_rsa_tempfile()


@pytest.mark.asyncio
async def test_init_loop_retries_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection failures trigger backoff; success ends the inner retry loop."""
    client = FutuClient()
    client._creds = _make_creds()

    monkeypatch.setattr("sidecar_futu.futu_client.SysConfig", MagicMock())
    monkeypatch.setattr("sidecar_futu.futu_client._BACKOFF_BASE_S", 0.005)

    call_count = 0

    def fake_factory(**_: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("OpenD down")
        ctx = MagicMock()
        ctx.unlock_trade.return_value = (0, "OK")
        return ctx

    monkeypatch.setattr("sidecar_futu.futu_client.OpenSecTradeContext", fake_factory)

    task = asyncio.create_task(client._init_loop())
    while not client.gateway_connected:  # noqa: ASYNC110
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert client.gateway_connected is True
    assert call_count == 3
    client._cleanup_rsa_tempfile()


@pytest.mark.asyncio
async def test_configure_replaces_rsa_tempfile_on_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H3 cred rotation: prior tempfile is wiped + unlinked when Configure swaps creds."""
    client = FutuClient()
    monkeypatch.setattr("sidecar_futu.futu_client.SysConfig", MagicMock())

    written_paths: list[Path] = []

    async def stub_init_loop() -> None:
        client._write_rsa_tempfile()
        assert client._rsa_tempfile_path is not None
        written_paths.append(client._rsa_tempfile_path)
        await asyncio.Event().wait()

    monkeypatch.setattr(client, "_init_loop", stub_init_loop)

    req1 = MagicMock()
    req1.unlock_pwd_md5 = "0" * 32
    req1.rsa_priv_pem = _make_rsa_pem()
    req1.opend_host = "x"
    req1.opend_port = 11111
    req1.connection_id = "x"

    await client.configure(req1)
    while not written_paths:  # noqa: ASYNC110
        await asyncio.sleep(0.005)
    first_path = written_paths[0]
    assert first_path.exists()

    req2 = MagicMock()
    req2.unlock_pwd_md5 = "1" * 32
    req2.rsa_priv_pem = _make_rsa_pem()
    req2.opend_host = "x"
    req2.opend_port = 11111
    req2.connection_id = "x"

    await client.configure(req2)
    while len(written_paths) < 2:  # noqa: ASYNC110
        await asyncio.sleep(0.005)

    assert not first_path.exists()
    second_path = written_paths[1]
    assert second_path != first_path
    assert second_path.exists()

    if client._init_task is not None:
        client._init_task.cancel()
        try:
            await client._init_task
        except asyncio.CancelledError:
            pass
    client._cleanup_rsa_tempfile()
    assert not second_path.exists()
