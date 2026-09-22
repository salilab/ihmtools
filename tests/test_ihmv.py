"""Offline tests for the IHMV validation CLI. No network, no credentials."""

import sys
import types

import pytest

from ihmtools import _common as common
from ihmtools import ihmv


def row(state, details=None):
    return {"RID": "R", "Processing_Status": state, "Processing_Details": details}


# --------------------------------------------------------------------------
# exit-code classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize("states, expected", [
    (["Success", "Success"], 0),
    (["Success", "Error"], 1),
    (["Success", "In Progress"], 2),
    (["New"], 2),
    (["Reprocess"], 2),
    # Pending outranks Error so a mixed batch keeps polling instead of
    # stopping early on the one that already failed.
    (["Error", "New"], 2),
    (["New", "Error"], 2),
    ([None], 3),
    (["New", None], 3),
])
def test_status_code(states, expected):
    results = [("r%d" % i, row(s) if s else None) for i, s in enumerate(states)]
    assert ihmv.status_code(results) == expected


# --------------------------------------------------------------------------
# target resolution
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode_kwargs, expected", [
    # both tools default to production; only the live tests pin dev
    ({}, ("https", "data.pdb-ihm.org", "101")),
    ({"mode": "dev"}, ("https", "data-dev.pdb-ihm.org", "199")),
    ({"mode": "production"}, ("https", "data.pdb-ihm.org", "101")),
    ({"mode": "production", "catalog": "7"}, ("https", "data.pdb-ihm.org", "7")),
    # --host overrides only the host; the catalog stays the default mode's
    ({"host": "example.org"}, ("https", "example.org", "101")),
    # deriva-py needs scheme and host apart, but --host still takes a whole URL
    ({"host": "http://plain.example"}, ("http", "plain.example", "101")),
    ({"host": "https://data-dev.pdb-ihm.org/"}, ("https", "data-dev.pdb-ihm.org", "101")),
])
def test_configure(monkeypatch, mode_kwargs, expected):
    monkeypatch.delenv("IHMV_HOST", raising=False)
    monkeypatch.delenv("IHMV_CATALOG", raising=False)
    ihmv.configure(types.SimpleNamespace(**mode_kwargs))
    assert (common.SCHEME, common.HOST, common.CATALOG_ID) == expected


def test_configure_flag_beats_env(monkeypatch):
    monkeypatch.setenv("IHMV_HOST", "https://env.example")
    ihmv.configure(types.SimpleNamespace(host="flag.example"))
    assert common.HOST == "flag.example"


def test_configure_mode_beats_env(monkeypatch):
    monkeypatch.setenv("IHMV_HOST", "https://env.example")
    ihmv.configure(types.SimpleNamespace(mode="production"))
    assert common.HOST == "data.pdb-ihm.org"


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------

def test_settable_excludes_backend_owned_states():
    """Processing_Status has no FK, so a bad value would be written silently."""
    assert set(ihmv.SETTABLE) == {"New", "Reprocess"}
    for backend_owned in ("Success", "Error", "In Progress"):
        assert backend_owned not in ihmv.SETTABLE


# --------------------------------------------------------------------------
# CLI parsing
# --------------------------------------------------------------------------

def parse(argv):
    """Parse without running anything, by intercepting the dispatch."""
    import contextlib
    import io
    captured = {}
    real = {}
    for name in ("do_status", "do_run", "do_download", "do_upload",
                 "do_set_status", "do_delete"):
        real[name] = getattr(ihmv, name)
        setattr(ihmv, name, lambda a, _n=name: captured.update(func=_n, args=a))
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            ihmv.main()
    finally:
        for name, fn in real.items():
            setattr(ihmv, name, fn)
    return captured["args"]


@pytest.mark.parametrize("argv, rids", [
    # A numeric RID after --wait used to be eaten as the poll interval, which
    # left no RIDs and silently listed everything with exit 0.
    (["get_status", "--wait", "300"], ["300"]),
    (["get_status", "--wait", "2Y0"], ["2Y0"]),
    (["get_status", "--wait", "-"], ["-"]),
    (["get_status", "300", "--wait"], ["300"]),
    (["get_status", "--wait", "--interval", "60", "300"], ["300"]),
])
def test_wait_never_swallows_a_rid(monkeypatch, argv, rids):
    monkeypatch.setattr(sys, "argv", ["ihmv"] + argv)
    args = parse(argv)
    assert args.rids == rids
    assert args.wait is True


def test_wait_defaults_and_overrides(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ihmv", "get_status", "--wait", "300"])
    assert parse(None).interval == common.POLL_INTERVAL
    monkeypatch.setattr(sys, "argv",
                        ["ihmv", "get_status", "--wait", "--interval", "5", "300"])
    assert parse(None).interval == 5


def test_no_wait_by_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ihmv", "get_status", "300"])
    args = parse(None)
    assert args.rids == ["300"] and args.wait is False

@pytest.mark.parametrize("argv", [
    ["upload", "model.cif", "--salt"],
    ["upload", "--salt", "model.cif"],
    ["run", "--salt", "model.cif"],
])
def test_salt_never_swallows_the_filename(monkeypatch, argv):
    """--salt takes no value precisely so it cannot eat the positional."""
    monkeypatch.setattr(sys, "argv", ["ihmv"] + argv)
    args = parse(argv)
    assert args.file == "model.cif"
    assert args.salt is True


def test_no_salt_by_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ihmv", "upload", "model.cif"])
    assert parse(["upload", "model.cif"]).salt is False


def test_broken_pipe_is_not_a_traceback(monkeypatch, capsys):
    """`ihmv get_status | head` closes the pipe; that is normal, not a crash."""
    def explode(_args):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(ihmv, "do_status", explode)
    monkeypatch.setattr(sys, "argv", ["ihmv", "get_status"])
    with pytest.raises(SystemExit) as caught:
        ihmv.main()
    # 128 + SIGPIPE, the status a program killed by SIGPIPE reports
    assert caught.value.code == 141
    assert "Traceback" not in capsys.readouterr().err


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------

def test_delete_skips_the_report_sweep_when_there_are_none(monkeypatch):
    """ERMrest answers a delete matching nothing with 404, so an entry the
    pipeline has not finished with yet could not be deleted at all."""
    deleted = []
    catalog = types.SimpleNamespace(delete=lambda path, **kw: deleted.append(path))
    monkeypatch.setattr(ihmv, "connect", lambda: (catalog, None))
    monkeypatch.setattr(common, "get", lambda _c, path: (
        [] if "Generated_File" in path
        else [{"RID": "R", "Title": "t", "Processing_Status": "In Progress",
               "File_MD5": "m", "File_URL": "/hatrac/x"}]))

    ihmv.do_delete(types.SimpleNamespace(rids=["R"], rid_flags=None, yes=True,
                                         purge_file=False))
    assert not any("Generated_File" in p for p in deleted)
    assert any("Structure_mmCIF/RID=R" in p for p in deleted)


def test_delete_sweeps_reports_when_there_are_some(monkeypatch):
    deleted = []
    catalog = types.SimpleNamespace(delete=lambda path, **kw: deleted.append(path))
    monkeypatch.setattr(ihmv, "connect", lambda: (catalog, None))
    monkeypatch.setattr(common, "get", lambda _c, path: (
        [{"Structure_mmCIF": "R"}] if "Generated_File" in path
        else [{"RID": "R", "Title": "t", "Processing_Status": "Success",
               "File_MD5": "m", "File_URL": "/hatrac/x"}]))

    ihmv.do_delete(types.SimpleNamespace(rids=["R"], rid_flags=None, yes=True,
                                         purge_file=False))
    # Reports first: a failure on the second call must leave the record behind
    # to retry, not orphan its reports.
    assert [("Generated_File" in p) for p in deleted] == [True, False]
