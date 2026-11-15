"""Distinguish occupied ports from denied local socket permissions."""

import errno
import sys
from unittest.mock import MagicMock

import pytest

from crawler.web import preview


@pytest.mark.parametrize(
    ("code", "message"),
    [(errno.EADDRINUSE, "Port belegt"), (errno.EPERM, "Sandbox prüfen")],
)
def test_preview_socket_failure_is_actionable(monkeypatch, tmp_path, code, message):
    monkeypatch.setattr(sys, "argv", ["preview", "--manifest", str(tmp_path / "manifest.json")])
    monkeypatch.setattr(
        preview.shutil, "disk_usage", lambda _: type("Disk", (), {"free": 20 * 1024**3})()
    )
    socket = MagicMock()
    socket.__enter__.return_value.bind.side_effect = OSError(code, "fixture")
    monkeypatch.setattr(preview.socket, "socket", lambda: socket)
    with pytest.raises(SystemExit, match=message):
        preview.main()
