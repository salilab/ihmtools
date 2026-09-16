"""Offline tests for the deposition CLI. No network, no credentials."""

import io
import sys
import types

import pytest

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
    ({}, "https://data.pdb-ihm.org/ermrest/catalog/1"),
    ({"mode": "production"}, "https://data.pdb-ihm.org/ermrest/catalog/1"),
    ({"mode": "dev"}, "https://data-dev.pdb-ihm.org/ermrest/catalog/99"),
    ({"host": "example.org"}, "https://example.org/ermrest/catalog/1"),
])
def test_configure(monkeypatch, kwargs, expected):
    monkeypatch.delenv("IHMDEP_HOST", raising=False)
    monkeypatch.delenv("IHMDEP_CATALOG", raising=False)
    ihmdep.configure(types.SimpleNamespace(**kwargs))
    assert ihmdep.CAT == expected


def _asset(md5_column, prefix):
    return {
        "md5": md5_column,
        "url_pattern": (
            prefix + '/uid/{{#if _RCB}}{{#regexFindFirst _RCB "[^/]+$"}}{{this}}'
            '{{/regexFindFirst}}{{else}}{{#regexFindFirst $session.client.id "[^/]+$"}}'
            '{{this}}{{/regexFindFirst}}{{/if}}/entry/x/{{{%s}}}{{{_%s.filename_ext}}}'
            % (md5_column, md5_column.replace("_MD5", "_URL"))
        ),
    }


def test_hatrac_target_handles_both_asset_columns():
    """mmCIF and image columns name their own md5 field."""
    mmcif = ihmdep.hatrac_target(_asset("mmCIF_File_MD5", "/hatrac/pdb/submitted"),
                                 "UID", "MD5", ".cif")
    image = ihmdep.hatrac_target(_asset("Image_File_MD5", "/hatrac/pdb/submitted"),
                                 "UID", "MD5", ".png")
    assert mmcif == "/hatrac/pdb/submitted/uid/UID/entry/x/MD5.cif"
    assert image == "/hatrac/pdb/submitted/uid/UID/entry/x/MD5.png"


def test_hatrac_target_refuses_unknown_field():
    bad = {"md5": "mmCIF_File_MD5", "url_pattern": "/hatrac/x/{{{Unexpected}}}"}
    with pytest.raises(SystemExit):
        ihmdep.hatrac_target(bad, "UID", "MD5", ".cif")


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
    assert parse(None).interval == 30
    monkeypatch.setattr(sys, "argv",
                        ["ihmdep", "get_status", "--wait", "--interval", "5", "300"])
    assert parse(None).interval == 5


def test_no_wait_by_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ihmdep", "get_status", "300"])
    args = parse(None)
    assert args.rids == ["300"] and args.wait is False


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
# logout
# --------------------------------------------------------------------------

TOKEN = {"access_token": "a", "refresh_token": "r", "expires_at_seconds": 9e11,
         "scope": "https://auth.globus.org/scopes/x/deriva_all"}


def logged_in(tmp_path, monkeypatch):
    import json as _json
    store = tmp_path / "tokens.json"
    store.write_text(_json.dumps(TOKEN))
    monkeypatch.setattr(ihmdep, "OUR_TOKENS", str(store))
    monkeypatch.setattr(ihmdep, "DERIVA_TOKENS", str(tmp_path / "absent.json"))
    return store


def test_logout_revokes_then_deletes(tmp_path, monkeypatch, capsys):
    store = logged_in(tmp_path, monkeypatch)
    revoked = []
    monkeypatch.setattr(ihmdep, "_revoke", lambda t: revoked.append(t) or True)

    ihmdep.do_logout(types.SimpleNamespace(local=False))
    assert sorted(revoked) == ["a", "r"], "both tokens must be revoked"
    assert not store.exists()


def test_logout_local_does_not_contact_globus(tmp_path, monkeypatch):
    store = logged_in(tmp_path, monkeypatch)

    def fail(_token):
        raise AssertionError("--local must not reach the network")

    monkeypatch.setattr(ihmdep, "_revoke", fail)
    ihmdep.do_logout(types.SimpleNamespace(local=True))
    assert not store.exists()


def test_logout_deletes_even_if_revocation_fails(tmp_path, monkeypatch, capsys):
    """The user asked to be logged out; an unreachable Globus must not block it."""
    store = logged_in(tmp_path, monkeypatch)
    monkeypatch.setattr(ihmdep, "_revoke", lambda t: False)

    ihmdep.do_logout(types.SimpleNamespace(local=False))
    assert not store.exists()
    assert "could not revoke" in capsys.readouterr().err


def test_logout_when_not_logged_in(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ihmdep, "OUR_TOKENS", str(tmp_path / "absent.json"))
    monkeypatch.setattr(ihmdep, "DERIVA_TOKENS", str(tmp_path / "also-absent.json"))
    ihmdep.do_logout(types.SimpleNamespace(local=False))
    assert "not logged in" in capsys.readouterr().err


def test_logout_reports_derivas_own_store(tmp_path, monkeypatch, capsys):
    """We read deriva-py's file but never write it, so we must not delete it."""
    import json as _json
    deriva = tmp_path / "deriva.json"
    deriva.write_text(_json.dumps({"usc_isrd": TOKEN}))
    monkeypatch.setattr(ihmdep, "OUR_TOKENS", str(tmp_path / "absent.json"))
    monkeypatch.setattr(ihmdep, "DERIVA_TOKENS", str(deriva))

    ihmdep.do_logout(types.SimpleNamespace(local=False))
    assert deriva.exists(), "deriva-py's store is not ours to remove"
    assert "deriva-globus-auth-utils logout" in capsys.readouterr().err
