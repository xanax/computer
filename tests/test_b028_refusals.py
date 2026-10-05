"""B-028: a probe that names a service's port must not be refused as a duplicate.

`refusal_for_command` answers "Service X is already running, do not start another
copy" for any `run_command` whose text mentions a declared service's port. A
diagnostic against a live service (`ss -ltn | grep 8181`) is such a command, so
the guard blocked all debugging of the thing it protects -- while a real duplicate
with a path or trailing space slipped through. Both regressions are pinned here.
"""
import pytest

from cptr.utils.services import command_uses_port, is_server_command


PORT = 8181

# (command, expected) -- expected True means "this looks like a bind on the port".
REFUSED_AS_DUPLICATE = [
    # A probe is a question, not a start. These were all wrongly refused.
    ("ss -ltn | grep 8181", False),
    ('ss -ltn | grep ":8181 "', False),
    ("ss -ltn | grep :8181 | head -2", False),
    ("curl -s http://127.0.0.1:8181/health", False),
    ("curl -s http://127.0.0.1:8181", False),
    ("curl -s -o /dev/null -D - -H 'Host: x' http://172.22.64.1:8181/health", False),
    ("lsof -i :8181", False),
    ("netstat -tlnp | grep 8181", False),
    ("ps -ef | grep 8181", False),
    ("echo listening; ss -ltn | grep 8181", False),
    # An unrecognised first stage (`python3 ... | grep`) is NOT exempt: when the
    # guard cannot tell a probe from a start it keeps the port match, so a real
    # duplicate is still stopped.
    ("python3 -c 'print(1)' | grep 8181", True),
    # Real binds must still be caught, including the shapes that used to slip past.
    ("node scripts/kindle-host-server.js 8181 123757", True),
    ("python -m http.server 8181", True),
    ("uvicorn app:app --port 8181", True),
    ("vite --port 8181", True),
    ("http-server -p 8181", True),
    ("python -m http.server 8181/", True),  # trailing path used to defeat it
    ('ss -ltn | grep "8181" && npm run dev -- --port 8181', True),
]


@pytest.mark.parametrize("command,expected", REFUSED_AS_DUPLICATE)
def test_command_uses_port(command, expected):
    assert command_uses_port(command, PORT) is expected


def test_a_real_server_start_is_not_exempt_merely_because_it_is_first():
    """`ss` later in the line must not make an actual bind look read-only."""
    assert command_uses_port("ss -ltn && vite --port 8181", PORT) is True


def test_probe_of_a_different_port_is_still_a_probe():
    assert command_uses_port("ss -ltn | grep 9999", 8181) is False


def test_no_port_configured_never_matches():
    assert command_uses_port("ss -ltn | grep 8181", None) is False


def test_read_only_exemption_requires_a_probe_at_all():
    """The exemption is the first stage only; `echo` alone should not blanket-exempt."""
    # `echo` names the port but starts nothing, so still not a bind.
    assert command_uses_port("echo 8181", PORT) is False
    assert is_server_command("python -m http.server 8181") is True
