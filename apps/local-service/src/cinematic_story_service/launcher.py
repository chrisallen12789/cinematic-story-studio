from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import socket
import sys
import threading
from pathlib import Path
from typing import Any, NoReturn

import uvicorn

from cinematic_story_service.app import create_app
from cinematic_story_service.config import ServiceSettings
from cinematic_story_service.errors import ServiceError
from cinematic_story_service.util import PROTOCOL_VERSION

_MAX_BOOTSTRAP_BYTES = 2048
_MAX_NONCE_LENGTH = 256


def _fail(message: str, exit_code: int = 2) -> NoReturn:
    # Messages are fixed and never interpolate bootstrap content or personal paths.
    print(f"CSS_ERROR {message}", file=sys.stderr, flush=True)
    raise SystemExit(exit_code)


def _read_bootstrap() -> tuple[str, str]:
    raw_line = sys.stdin.buffer.readline(_MAX_BOOTSTRAP_BYTES + 1)
    if not raw_line or len(raw_line) > _MAX_BOOTSTRAP_BYTES or not raw_line.endswith(b"\n"):
        _fail("invalid_bootstrap")
    try:
        value: Any = json.loads(raw_line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("invalid_bootstrap")
    if not isinstance(value, dict) or set(value) != {"token", "nonce", "protocolVersion"}:
        _fail("invalid_bootstrap")
    token = value.get("token")
    nonce = value.get("nonce")
    protocol_version = value.get("protocolVersion")
    if not isinstance(protocol_version, str) or protocol_version != PROTOCOL_VERSION:
        _fail("incompatible_protocol")
    if not isinstance(token, str) or not _is_256_bit_token(token):
        _fail("invalid_bootstrap")
    if (
        not isinstance(nonce, str)
        or not 1 <= len(nonce) <= _MAX_NONCE_LENGTH
        or any(ord(character) < 33 for character in nonce)
    ):
        _fail("invalid_bootstrap")
    return token, nonce


def _is_256_bit_token(value: str) -> bool:
    try:
        if len(value) == 64:
            return len(bytes.fromhex(value)) == 32
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        return len(decoded) == 32
    except (ValueError, binascii.Error):
        return False


def _prebound_loopback_socket() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.set_inheritable(False)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        listener.setblocking(False)
        return listener
    except Exception:
        listener.close()
        raise


async def _serve(data_dir: Path, token: str, nonce: str) -> int:
    listener = _prebound_loopback_socket()
    settings = ServiceSettings(data_dir=data_dir, bearer_token=token).validated()
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_config=None,
        access_log=False,
        server_header=False,
        date_header=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve(sockets=[listener]))

    while not server.started:
        if serve_task.done():
            listener.close()
            return 1
        await asyncio.sleep(0.01)

    ready = {
        "port": listener.getsockname()[1],
        "instanceId": settings.instance_id,
        "nonce": nonce,
        "protocolVersion": PROTOCOL_VERSION,
    }
    print(
        f"CSS_READY {json.dumps(ready, separators=(',', ':'))}",
        file=sys.stdout,
        flush=True,
    )

    loop = asyncio.get_running_loop()
    stdin_closed = asyncio.Event()

    def watch_control_pipe() -> None:
        # Any trailing control data or EOF relinquishes child ownership and initiates shutdown.
        sys.stdin.buffer.read(1)
        loop.call_soon_threadsafe(stdin_closed.set)

    threading.Thread(
        target=watch_control_pipe,
        name="cinematic-story-control-pipe",
        daemon=True,
    ).start()
    eof_task = asyncio.create_task(stdin_closed.wait())
    done, pending = await asyncio.wait(
        {serve_task, eof_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if eof_task in done:
        server.should_exit = True
        await serve_task
    else:
        eof_task.cancel()
    for task in pending:
        if task is not serve_task:
            task.cancel()
    listener.close()
    return 0 if server.started else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="cinematic-story-service")
    parser.add_argument("--data-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    token, nonce = _read_bootstrap()
    try:
        return asyncio.run(_serve(args.data_dir, token, nonce))
    except KeyboardInterrupt:
        return 130
    except ServiceError as exc:
        if exc.code == "STORAGE_LOCKED":
            _fail("storage_locked", exit_code=1)
        if exc.code == "DATABASE_SCHEMA_UNSUPPORTED":
            _fail("incompatible_schema", exit_code=1)
        _fail("startup_failed", exit_code=1)
    except Exception:
        _fail("startup_failed", exit_code=1)


if __name__ == "__main__":
    raise SystemExit(main())
