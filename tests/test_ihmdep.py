"""Offline tests for the deposition CLI. No network, no credentials."""

import sys
import types

import pytest

from ihmtools import _common as common
from ihmtools import ihmdep


def entry(workflow, process=None, accession=None):
    return {"RID": "R", "Workflow_Status": workflow, "Process_Status": process,
            "Accession_Code": accession}


# --------------------------------------------------------------------------
# Process_Status classification -- by shape, since the vocabulary grows
# --------------------------------------------------------------------------

@pytest.mark.parametrize("state", [
    "In progress: processing uploaded mmCIF file",
    "In progress: generating mmCIF file",
    "In progress: processing uploaded restraint files",
    "New (trigger backend process)",
    "Reprocess (trigger backend process after Error)",
    "Resume (trigger backend process)",
    None,
])
def test_pending_states(state):
    assert ihmdep.is_pending(state)


@pytest.mark.parametrize("state", [
    "Error: processing uploaded mmCIF file",
    "Error: generating mmCIF file",
    "Error: releasing entry",
])
def test_error_states(state):
    assert ihmdep.is_error(state)
    assert not ihmdep.is_pending(state)


def test_success_is_neither():
    assert not ihmdep.is_pending("Success")
    assert not ihmdep.is_error("Success")


@pytest.mark.parametrize("rows, expected", [
    ([("DEPO", "Success")], 0),
    ([("DEPO", "Error: processing uploaded mmCIF file")], 1),
    ([("DEPO", "In progress: generating mmCIF file")], 2),
    ([("DRAFT", None)], 2),
    # Workflow_Status alone can carry the failure.
    ([("ERROR", "Success")], 1),
    # Pending outranks Error across a batch.
    ([("DEPO", "Error: generating mmCIF file"), ("DEPO", "In progress: releasing entry")], 2),
])
def test_status_code(rows, expected):
    results = [("r%d" % i, entry(w, p)) for i, (w, p) in enumerate(rows)]
    assert ihmdep.status_code(results) == expected


def test_status_code_unknown_outranks_all():
    results = [("a", entry("DEPO", "In progress: x")), ("b", None)]
    assert ihmdep.status_code(results) == 3


# --------------------------------------------------------------------------
# delete gate -- everything from SUBMIT onward must be refused
# --------------------------------------------------------------------------

@pytest.mark.parametrize("workflow", ["DRAFT", "DEPO"])
def test_deletable_before_the_backend_produces_a_record(workflow):
    assert ihmdep.not_deletable(entry(workflow)) is None


@pytest.mark.parametrize("workflow", [
    "RECORD READY", "SUBMIT", "mmCIF CREATED", "SUBMISSION COMPLETE",
    "HOLD", "RELEASE READY", "REL", "ABANDONED",
])
def test_not_deletable_once_processed(workflow):
    assert ihmdep.not_deletable(entry(workflow)) is not None


@pytest.mark.parametrize("process", [
    "Error: processing uploaded mmCIF file",     # failed during DEPO
    "Error: generating mmCIF file",              # failed after SUBMIT
    None,
])
def test_error_is_never_deletable(process):
    """ERROR is out regardless of where it failed."""
    assert ihmdep.not_deletable(entry("ERROR", process)) is not None


@pytest.mark.parametrize("workflow", ["DRAFT", "DEPO"])
def test_accession_code_blocks_even_pre_submit(workflow):
    why = ihmdep.not_deletable(entry(workflow, accession="9XYZ"))
    assert why is not None and "9XYZ" in why


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------

def test_only_two_transitions_are_settable():
    """A depositor drives DRAFT -> DEPO and RECORD READY -> SUBMIT, nothing else."""
    assert ihmdep.TRANSITIONS == {"DEPO": "DRAFT", "SUBMIT": "RECORD READY"}
    assert set(ihmdep.USER_SETTABLE) == {"DEPO", "SUBMIT"}
    for backend_or_curator in ("REL", "RECORD READY", "ABANDONED", "DRAFT"):
        assert backend_or_curator not in ihmdep.USER_SETTABLE


def test_image_extension_restriction_is_ours():
    """The Image_File_URL annotation carries no filename_ext_filter."""
    assert ihmdep.IMAGE_EXT == (".png", ".PNG")


# --------------------------------------------------------------------------
# target resolution
# --------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs, expected", [
    # production is the default for the deposition system
    ({}, ("https", "data.pdb-ihm.org", "1")),
    ({"mode": "production"}, ("https", "data.pdb-ihm.org", "1")),
    ({"mode": "dev"}, ("https", "data-dev.pdb-ihm.org", "99")),
    ({"host": "example.org"}, ("https", "example.org", "1")),
    # deriva-py needs scheme and host apart, but --host still takes a whole URL
    ({"host": "http://plain.example"}, ("http", "plain.example", "1")),
    ({"host": "https://data-dev.pdb-ihm.org/"}, ("https", "data-dev.pdb-ihm.org", "1")),
])
def test_configure(monkeypatch, kwargs, expected):
    monkeypatch.delenv("IHMDEP_HOST", raising=False)
    monkeypatch.delenv("IHMDEP_CATALOG", raising=False)
    ihmdep.configure(types.SimpleNamespace(**kwargs))
    assert (common.SCHEME, common.HOST, common.CATALOG_ID) == expected


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
        real[name] = getattr(ihmdep, name)
        setattr(ihmdep, name, lambda a, _n=name: captured.update(func=_n, args=a))
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            ihmdep.main()
    finally:
        for name, fn in real.items():
            setattr(ihmdep, name, fn)
    return captured["args"]


@pytest.mark.parametrize("argv, rids", [
    # A numeric RID after --wait used to be eaten as the poll interval, which
    # left no RIDs and silently listed everything with exit 0.
    (["get_status", "--wait", "300"], ["300"]),
    (["get_status", "--wait", "9-DXAM"], ["9-DXAM"]),
    (["get_status", "--wait", "-"], ["-"]),
    (["get_status", "300", "--wait"], ["300"]),
    (["get_status", "--wait", "--interval", "60", "300"], ["300"]),
])
def test_wait_never_swallows_a_rid(monkeypatch, argv, rids):
    monkeypatch.setattr(sys, "argv", ["ihmdep"] + argv)
    args = parse(argv)
    assert args.rids == rids
    assert args.wait is True


def test_wait_defaults_and_overrides(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ihmdep", "get_status", "--wait", "300"])
    assert parse(None).interval == common.POLL_INTERVAL
    monkeypatch.setattr(sys, "argv",
                        ["ihmdep", "get_status", "--wait", "--interval", "5", "300"])
    assert parse(None).interval == 5


def test_no_wait_by_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ihmdep", "get_status", "300"])
    args = parse(None)
    assert args.rids == ["300"] and args.wait is False

@pytest.mark.parametrize("argv", [
    ["upload", "model.cif", "--salt"],
    ["upload", "--salt", "model.cif"],
    ["run", "--salt", "model.cif"],
])
def test_salt_never_swallows_the_filename(monkeypatch, argv):
    """--salt takes no value precisely so it cannot eat the positional."""
    monkeypatch.setattr(sys, "argv", ["ihmdep"] + argv)
    args = parse(argv)
    assert args.file == "model.cif"
    assert args.salt is True


def test_no_salt_by_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ihmdep", "upload", "model.cif"])
    assert parse(["upload", "model.cif"]).salt is False


def test_broken_pipe_is_not_a_traceback(monkeypatch, capsys):
    """`ihmdep get_status -a | head` closes the pipe; that is normal."""
    def explode(_args):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(ihmdep, "do_status", explode)
    monkeypatch.setattr(sys, "argv", ["ihmdep", "get_status", "-a"])
    with pytest.raises(SystemExit) as caught:
        ihmdep.main()
    assert caught.value.code == 141
    assert "Traceback" not in capsys.readouterr().err


# --------------------------------------------------------------------------
# download: which generated files a set of selectors asks for
# --------------------------------------------------------------------------

FULL = "Validation: Full PDF"
SUMMARY = "Validation: Summary PDF"
MMCIF = "mmCIF"


@pytest.mark.parametrize("flags, expected", [
    # No selector: everything the pipeline generated. The mmCIF used to be
    # left out here, so an entry that had only its mmCIF -- most of them, for
    # most of their life -- reported "nothing to download yet".
    ([], {FULL, SUMMARY, MMCIF}),
    (["mmcif"], {MMCIF}),           # used to mean "the default set, and also"
    (["full"], {FULL}),
    (["summary"], {SUMMARY}),
    (["full", "summary"], {FULL, SUMMARY}),
    (["mmcif", "full"], {MMCIF, FULL}),
    # --logs selects from the error table, so it asks for no generated file,
    # but it still counts as a selector: it must not fall back to everything.
    (["logs"], set()),
    (["mmcif", "logs"], {MMCIF}),
])
def test_download_selectors(flags, expected):
    args = types.SimpleNamespace(**{k: k in flags for k in ihmdep.SELECTORS})
    assert set(ihmdep.wanted_types(args)) == expected


@pytest.mark.parametrize("flag", ihmdep.SELECTORS)
def test_download_flags_match_the_selectors(monkeypatch, flag):
    """The parser and the selector table must not drift apart."""
    argv = ["download", "R", "--" + flag]
    monkeypatch.setattr(sys, "argv", ["ihmdep"] + argv)
    args = parse(argv)
    assert getattr(args, flag) is True
    assert all(not getattr(args, other)
               for other in ihmdep.SELECTORS if other != flag)


# --------------------------------------------------------------------------
# get_status: which status column is reported
# --------------------------------------------------------------------------

def status_out(monkeypatch, capsys, rows, *flags):
    """Run do_status over canned rows and return what lands on stdout."""
    results = [(r["RID"], r) for r in rows]
    monkeypatch.setattr(ihmdep, "poll_status", lambda _r, _v: results)
    args = types.SimpleNamespace(
        rids=[r["RID"] for r in rows], rid_flags=None, verbose=False,
        wait=False, interval=1,
        **{name: name in flags for name, _, _ in ihmdep.STATUS_FIELDS})
    with pytest.raises(SystemExit):
        ihmdep.do_status(args)
    return capsys.readouterr().out.strip()


READY = {"RID": "A", "Workflow_Status": "RECORD READY", "Process_Status": "Success"}
CREATED = {"RID": "B", "Workflow_Status": "mmCIF CREATED", "Process_Status": "Success"}


def test_single_rid_still_prints_the_bare_process_word(monkeypatch, capsys):
    """$(ihmdep get_status RID) is a documented contract; it must not change."""
    assert status_out(monkeypatch, capsys, [READY]) == "Success"


def test_single_rid_can_report_the_workflow_instead(monkeypatch, capsys):
    """'Success' is the same word after DEPO and after SUBMIT -- only the
    workflow state says which run it belonged to."""
    assert status_out(monkeypatch, capsys, [READY], "workflow") == "RECORD READY"
    assert status_out(monkeypatch, capsys, [CREATED], "workflow") == "mmCIF CREATED"


def test_single_rid_can_report_both(monkeypatch, capsys):
    out = status_out(monkeypatch, capsys, [CREATED], "workflow", "process")
    assert out.split("\t") == ["mmCIF CREATED", "Success"]


def test_several_rids_report_both_by_default(monkeypatch, capsys):
    lines = status_out(monkeypatch, capsys, [READY, CREATED]).splitlines()
    assert lines[0].split("\t") == ["#RID", "WORKFLOW", "PROCESS"]
    assert lines[1].split("\t") == ["A", "RECORD READY", "Success"]


def test_several_rids_narrow_to_the_asked_column(monkeypatch, capsys):
    lines = status_out(monkeypatch, capsys, [READY, CREATED], "workflow").splitlines()
    assert lines[0].split("\t") == ["#RID", "WORKFLOW"]
    assert [l.split("\t")[1] for l in lines[1:]] == ["RECORD READY", "mmCIF CREATED"]


@pytest.mark.parametrize("flag", ["workflow", "process"])
def test_status_flags_parse(monkeypatch, flag):
    argv = ["get_status", "R", "--" + flag]
    monkeypatch.setattr(sys, "argv", ["ihmdep"] + argv)
    args = parse(argv)
    assert getattr(args, flag) is True
    assert args.rids == ["R"], "a status flag must not swallow the RID"
