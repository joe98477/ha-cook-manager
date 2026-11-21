"""Test setup."""

import sys

import pytest

if sys.platform == "win32":
    # The Windows event loop needs a socket pair, which the Home Assistant test
    # plugin blocks before every test. Leave sockets alone on Windows.
    import pytest_socket

    pytest_socket.disable_socket = lambda *args, **kwargs: None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load the integration from custom_components."""
    return
