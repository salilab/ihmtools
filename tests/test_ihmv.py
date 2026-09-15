"""Offline tests for the IHMV validation CLI. No network, no credentials."""

import io
import sys
import types

import pytest

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
    ({}, "https://data-dev.pdb-ihm.org/ermrest/catalog/199"),
    ({"mode": "dev"}, "https://data-dev.pdb-ihm.org/ermrest/catalog/199"),
    ({"mode": "production"}, "https://data.pdb-ihm.org/ermrest/catalog/101"),
    ({"mode": "production", "catalog": "7"}, "https://data.pdb-ihm.org/ermrest/catalog/7"),
    ({"host": "example.org"}, "https://example.org/ermrest/catalog/199"),
    # a bare hostname gets a scheme
    ({"host": "http://plain.example"}, "http://plain.example/ermrest/catalog/199"),
])
def test_configure(monkeypatch, mode_kwargs, expected):
    monkeypatch.delenv("IHMV_HOST", raising=False)
    monkeypatch.delenv("IHMV_CATALOG", raising=False)
    ihmv.configure(types.SimpleNamespace(**mode_kwargs))
    assert ihmv.CAT == expected


def test_configure_flag_beats_env(monkeypatch):
    monkeypatch.setenv("IHMV_HOST", "https://env.example")
    ihmv.configure(types.SimpleNamespace(host="flag.example"))
    assert ihmv.CAT.startswith("https://flag.example")


def test_configure_mode_beats_env(monkeypatch):
    monkeypatch.setenv("IHMV_HOST", "https://env.example")
    ihmv.configure(types.SimpleNamespace(mode="production"))
    assert ihmv.CAT.startswith("https://data.pdb-ihm.org")


# --------------------------------------------------------------------------
# hatrac url_pattern resolution
# --------------------------------------------------------------------------

ASSET = {
    "md5": "File_MD5",
    "url_pattern": (
        '/hatrac/dev/ihmv/submitted/uid/{{#if _RCB}}'
        '{{#regexFindFirst _RCB "[^/]+$"}}{{this}}{{/regexFindFirst}}{{else}}'
        '{{#regexFindFirst $session.client.id "[^/]+$"}}{{this}}{{/regexFindFirst}}'
        '{{/if}}/structure/mmCIF/{{{File_MD5}}}{{{_File_URL.filename_ext}}}'
    ),
}


def test_hatrac_target_resolves():
    got = ihmv.hatrac_target(ASSET["url_pattern"], "UID", "MD5", ".cif")
    assert got == "/hatrac/dev/ihmv/submitted/uid/UID/structure/mmCIF/MD5.cif"
    assert "{{" not in got


def test_hatrac_target_refuses_unknown_field():
    """Guessing a path would put the file where the pipeline never looks."""
    with pytest.raises(SystemExit):
        ihmv.hatrac_target("/hatrac/x/{{{Some_New_Field}}}", "UID", "MD5", ".cif")


# --------------------------------------------------------------------------
# RID collection
# --------------------------------------------------------------------------

def args_with(rids=None, rid_flags=None):
    return types.SimpleNamespace(rids=rids or [], rid_flags=rid_flags)


def test_collect_rids_merges_and_dedupes():
    got = ihmv.collect_rids(args_with(["B", "A"], ["C", "A"]))
    assert got == ["B", "A", "C"]


def test_collect_rids_empty_means_list():
    assert ihmv.collect_rids(args_with()) == []


def test_collect_rids_reads_stdin_only_for_dash(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("X\nY\n"))
    assert ihmv.collect_rids(args_with(["-"])) == ["X", "Y"]


def test_collect_rids_dash_twice_does_not_reread(monkeypatch):
    """A second '-' would otherwise block on an already-exhausted stdin."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("X\n"))
    assert ihmv.collect_rids(args_with(["-", "-"])) == ["X"]


def test_collect_rids_dash_mixes_with_flags(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("X\n"))
    assert ihmv.collect_rids(args_with(["-"], ["Y"])) == ["X", "Y"]


def test_collect_rids_empty_stdin_is_an_error(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit):
        ihmv.collect_rids(args_with(["-"]))


# --------------------------------------------------------------------------
# output formatting
# --------------------------------------------------------------------------

class FakeOut(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


def render(tty, headers, rows, **kw):
    out = FakeOut(tty)
    real = sys.stdout
    sys.stdout = out
    try:
        ihmv.emit_table(headers, rows, **kw)
    finally:
        sys.stdout = real
    return out.getvalue().splitlines()


def test_emit_table_pipes_tab_separated():
    lines = render(False, ["RID", "STATUS"], [["A", "In Progress"]])
    assert lines[0] == "#RID\tSTATUS"
    # The whole point: a value containing a space stays one field.
    assert lines[1].split("\t") == ["A", "In Progress"]


def test_emit_table_aligns_on_a_terminal():
    lines = render(True, ["RID", "STATUS"], [["LONGER", "x"]])
    starts = [line.index("STATUS" if i == 0 else "x") for i, line in enumerate(lines)]
    assert starts[0] == starts[1], "header and data must share a column start"


def test_emit_table_notes_go_to_stderr(capsys):
    ihmv.emit_table(["RID"], [["A"]], notes=["the log"])
    captured = capsys.readouterr()
    assert "the log" not in captured.out, "stdout must stay parseable"
    assert "the log" in captured.err


def test_details_block_unescapes_literal_newlines():
    """The catalog stores logs with backslash-n rather than real newlines."""
    assert ihmv.details_block("a\\nb", indent="") == "a\nb"


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
                 "do_set_status", "do_delete", "do_login"):
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
    assert parse(None).interval == 30
    monkeypatch.setattr(sys, "argv",
                        ["ihmv", "get_status", "--wait", "--interval", "5", "300"])
    assert parse(None).interval == 5


def test_no_wait_by_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ihmv", "get_status", "300"])
    args = parse(None)
    assert args.rids == ["300"] and args.wait is False
