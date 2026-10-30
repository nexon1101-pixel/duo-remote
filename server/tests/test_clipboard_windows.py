"""Windowsの本物のクリップボードを読み書きする（GitHub Actions の windows-latest で動く）。"""
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windowsだけ")


def test_clipboard_round_trip():
    from duo_server.input_win import WinInput

    backend = WinInput()
    text = "duo-remote のテスト\r\n2行目 😀"
    backend.clip_set(text)
    assert backend.clip_get() == text
