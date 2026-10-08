"""Offline tests for the admin re-upload guards. No network, no credentials.

The guards are the whole point of the command: it writes to entries that are
past deposition, where the backend's own recovery path would otherwise route a
changed file into the insert-only loader.
"""

import pytest

from ihmtools import ihmdep


def entry(process, manual=True, md5="old", workflow="ERROR"):
    """One PDB:entry row as the catalog returns it.

    `manual=None` stands for the column being absent from the response, which
    is what an account that cannot see Manual_Processing gets.
    """
    row = {"RID": "R", "Workflow_Status": workflow, "Process_Status": process,
           "mmCIF_File_MD5": md5, "Accession_Code": "9XYZ"}
    if manual is not None:
        row["Manual_Processing"] = manual
    return row


# --------------------------------------------------------------------------
# which stage to re-arm: the inverse of export_mmCIF's error dispatch
# --------------------------------------------------------------------------

@pytest.mark.parametrize("process, stage", [
    ("Error: generating mmCIF file", "SUBMIT"),
    ("Error: generating system files", "SUBMISSION COMPLETE"),
    ("Error: releasing entry", "RELEASE READY"),
])
def test_stage_is_the_inverse_of_the_failure(process, stage):
    code, _, target = ihmdep.reupload_verdict(entry(process), "new")
    assert code == ihmdep.REUPLOAD_OK
    assert target == stage


def test_depo_stage_is_never_offered():
    """The one error that means the DEPO load itself failed.

    Re-arming at DEPO runs the insert-only loader against content that is
    already there, so this command refuses rather than offering it.
    """
    code, why, target = ihmdep.reupload_verdict(
        entry("Error: processing uploaded mmCIF file"), "new")
    assert code == ihmdep.REUPLOAD_REFUSED
    assert target is None
    assert "DEPO" in why


def test_an_entry_that_is_not_in_error_is_refused():
    code, why, _ = ihmdep.reupload_verdict(
        entry("Success", workflow="mmCIF CREATED"), "new")
    assert code == ihmdep.REUPLOAD_REFUSED
    assert "Success" in why


# --------------------------------------------------------------------------
# the switch
# --------------------------------------------------------------------------

def test_invisible_switch_is_an_environment_failure():
    """Code 4, so a bash loop breaks instead of failing identically 30 times."""
    code, why, _ = ihmdep.reupload_verdict(
        entry("Error: generating mmCIF file", manual=None), "new")
    assert code == ihmdep.REUPLOAD_ENVIRONMENT
    assert "Manual_Processing" in why


def test_switch_already_false_is_refused():
    """False means the worker may already have it; staging would not be inert."""
    code, why, _ = ihmdep.reupload_verdict(
        entry("Error: generating mmCIF file", manual=False), "new")
    assert code == ihmdep.REUPLOAD_REFUSED
    assert "Manual_Processing" in why


# --------------------------------------------------------------------------
# idempotence -- what makes an interrupted loop safe to re-run
# --------------------------------------------------------------------------

def test_same_md5_is_a_noop():
    code, _, _ = ihmdep.reupload_verdict(
        entry("Error: generating mmCIF file", md5="same"), "same")
    assert code == ihmdep.REUPLOAD_NOOP


def test_noop_is_checked_before_the_switch():
    """A finished entry has the switch at False *and* the md5 already matching.

    Checking the switch first would report every completed entry as refused,
    and a re-run of the same RID list could not tell "done" from "rejected".
    """
    code, _, _ = ihmdep.reupload_verdict(
        entry("Success", manual=False, md5="same", workflow="mmCIF CREATED"), "same")
    assert code == ihmdep.REUPLOAD_NOOP


def test_unknown_rid():
    code, _, _ = ihmdep.reupload_verdict(None, "new")
    assert code == ihmdep.REUPLOAD_UNKNOWN


# --------------------------------------------------------------------------
# the write itself: one statement, and it must not touch Last_mmCIF_File_MD5
# --------------------------------------------------------------------------

def test_payload_carries_the_swap_the_switch_and_the_stage():
    asset = {"URL": "/hatrac/x:V", "Name": "fixed.cif", "MD5": "abc", "Bytes": 12}
    row = ihmdep.reupload_row("9-ABCD", asset, "SUBMIT")
    assert row == {"RID": "9-ABCD",
                   "mmCIF_File_URL": "/hatrac/x:V", "mmCIF_File_Name": "fixed.cif",
                   "mmCIF_File_MD5": "abc", "mmCIF_File_Bytes": 12,
                   "Manual_Processing": False, "Workflow_Status": "SUBMIT"}


def test_payload_never_writes_the_backend_owned_md5():
    """The BEFORE UPDATE trigger moves the old md5 into Last_mmCIF_File_MD5.

    Writing it from a client overwrites the server's own bookkeeping.
    """
    row = ihmdep.reupload_row("9-ABCD", {"URL": "u", "Name": "n", "MD5": "m", "Bytes": 1},
                              "SUBMIT")
    assert "Last_mmCIF_File_MD5" not in row


def test_the_stage_never_stays_at_error():
    """Setting the file while NEW.Workflow_Status is still ERROR lets the
    trigger reroute the entry to DEPO -- the path this command exists to avoid."""
    for stage in ihmdep.REUPLOAD_STAGE.values():
        row = ihmdep.reupload_row("R", {"URL": "u", "Name": "n", "MD5": "m", "Bytes": 1}, stage)
        assert row["Workflow_Status"] != "ERROR"


# --------------------------------------------------------------------------
# the command: seams patched, as the rest of the suite does it
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeCatalog:
    """Records PUTs and answers them with whatever `matched` says."""

    def __init__(self, matched=None):
        self.puts = []
        self.matched = matched

    def put(self, url, json):
        self.puts.append((url, json))
        return FakeResponse(self.matched if self.matched is not None else json)


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """connect/whoami/asset-annotation/upload stubbed; returns the fake catalog."""
    cif = tmp_path / "fixed.cif"
    cif.write_text("data_X\n")
    cat = FakeCatalog()
    monkeypatch.setattr(ihmdep, "connect", lambda: (cat, "store"))
    monkeypatch.setattr(ihmdep, "whoami", lambda _c: "https://auth.globus.org/UID")
    monkeypatch.setattr(ihmdep, "get", lambda _c, _p: {"url_pattern": "/hatrac/{{{md5}}}{{{_x.filename_ext}}}",
                                                       "md5": "md5"})
    monkeypatch.setattr(ihmdep, "prepare_asset",
                        lambda *a, **k: {"path": str(cif), "name": "fixed.cif", "ext": ".cif",
                                         "size": 7, "md5": "newmd5", "md5_b64": "x"})
    monkeypatch.setattr(ihmdep, "put_asset",
                        lambda *a, **k: {"URL": "/hatrac/newmd5.cif:VER", "Name": "fixed.cif",
                                         "MD5": "newmd5", "Bytes": 7})
    cat.cif = str(cif)
    return cat


def args_for(cat, **kw):
    import types as _t
    base = dict(rid="9-ABCD", file=cat.cif, dry_run=False, yes=True)
    base.update(kw)
    return _t.SimpleNamespace(**base)


def test_invisible_column_falls_back_to_a_row_without_it(monkeypatch):
    """Projecting an admin-only column 409s, which must not look like a dead catalog."""
    import requests
    calls = []

    def fake_get(_cat, path):
        calls.append(path)
        if "Manual_Processing" in path:
            r = requests.Response()
            r.status_code = 409
            raise requests.HTTPError(response=r)
        return [{"RID": "9-ABCD", "Process_Status": "Error: generating mmCIF file"}]

    monkeypatch.setattr(ihmdep, "get", fake_get)
    row = ihmdep.reupload_entry("cat", "9-ABCD")
    assert "Manual_Processing" not in row
    assert len(calls) == 2 and "Manual_Processing" in calls[0]


def test_a_refusal_writes_nothing_and_exits_with_its_code(wired, monkeypatch, capsys):
    monkeypatch.setattr(ihmdep, "reupload_entry",
                        lambda *a: entry("Success", workflow="mmCIF CREATED"))
    with pytest.raises(SystemExit) as e:
        ihmdep.do_reupload(args_for(wired))
    assert e.value.code == ihmdep.REUPLOAD_REFUSED
    assert wired.puts == []


def test_dry_run_writes_nothing_and_names_the_stage(wired, monkeypatch, capsys):
    monkeypatch.setattr(ihmdep, "reupload_entry",
                        lambda *a: entry("Error: generating system files"))
    ihmdep.do_reupload(args_for(wired, dry_run=True))
    assert wired.puts == []
    assert "SUBMISSION COMPLETE" in capsys.readouterr().err


def test_the_happy_path_sends_exactly_one_put(wired, monkeypatch, capsys):
    monkeypatch.setattr(ihmdep, "reupload_entry",
                        lambda *a: entry("Error: generating mmCIF file"))
    ihmdep.do_reupload(args_for(wired))
    assert len(wired.puts) == 1
    url, payload = wired.puts[0]
    assert url.startswith("/attributegroup/PDB:entry/RID;")
    assert payload == [ihmdep.reupload_row("9-ABCD", {"URL": "/hatrac/newmd5.cif:VER",
                                                      "Name": "fixed.cif", "MD5": "newmd5",
                                                      "Bytes": 7}, "SUBMIT")]
    assert capsys.readouterr().out.strip() == "9-ABCD"


def test_a_put_that_matched_no_row_is_not_reported_as_success(wired, monkeypatch):
    """ERMrest answers an attributegroup PUT for an unknown RID with 200 and an
    empty list, so the status code alone cannot be trusted."""
    wired.matched = []
    monkeypatch.setattr(ihmdep, "reupload_entry",
                        lambda *a: entry("Error: generating mmCIF file"))
    with pytest.raises(SystemExit) as e:
        ihmdep.do_reupload(args_for(wired))
    assert e.value.code not in (0, None)
