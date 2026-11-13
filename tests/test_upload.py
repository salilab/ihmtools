"""Offline tests for `ihmdep upload`: the insert of the PDB:entry row.

The fake stands in for the catalog, not for ihmdep: it applies ERMrest's own
rule for an insert, so these tests fail when ihmdep stops telling the server
which columns to fill in by itself.
"""

import os
import types
from urllib.parse import unquote as urlunquote

import pytest
import requests

from ihmtools import ihmdep

# PDB:entry as production serves it to a submitter (2026-11): column -> may insert.
PRODUCTION = {
    "RID": True, "RCT": True, "RMT": False, "RCB": True, "RMB": False, "id": False,
    "Image_File_URL": True, "Image_File_Name": True, "Image_File_MD5": True,
    "Image_File_Bytes": True,
    "mmCIF_File_URL": True, "mmCIF_File_Name": True, "mmCIF_File_MD5": True,
    "mmCIF_File_Bytes": True,
    "Workflow_Status": True, "Process_Status": True, "Record_Status_Detail": False,
    "Accession_Code": False, "Last_mmCIF_File_MD5": True, "Release_Date": False,
    "Deposit_Date": False, "Submitter_Flag": False, "Submitter_Flag_Date": False,
    "Method_Details": True,
}
# The dev catalog gained a backend-filled column in October 2026, and every
# upload was refused until ihmdep stopped hard-coding the list.
DEV = dict(PRODUCTION, Accession_Issue_Time=False)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def refusal(status, url):
    response = requests.Response()
    response.status_code = status
    response.url = url
    response.reason = {403: "Forbidden", 409: "Conflict"}[status]
    return requests.HTTPError(response=response)


class EntryCatalog:
    """A catalog whose PDB:entry accepts an insert as ERMrest does.

    A column that is in neither the row nor `defaults=` counts as supplied by
    the client, as NULL. Supplying a column the client may not insert is
    refused with 403, and so is a default the table does not have, with 409.
    """

    # Both servers, 2026-11: one entry per file and one per file name, per depositor.
    KEYS = [["RID"], ["id"], ["Accession_Code"], ["id", "RCB"],
            ["RCB", "mmCIF_File_MD5"], ["RCB", "mmCIF_File_Name"]]

    def __init__(self, columns, existing=()):
        self.columns = columns
        self.existing = list(existing)      # the depositor's entries already there
        self.inserts = []

    def get_authn_session(self):
        return FakeResponse({"client": {"id": "https://auth.globus.org/UID"}})

    def get(self, path):
        if path == "/schema/PDB/table/entry":
            return FakeResponse({
                "column_definitions": [{"name": name, "rights": {"insert": allowed}}
                                       for name, allowed in self.columns.items()],
                "keys": [{"unique_columns": k} for k in self.KEYS]})
        if "/annotation/" in path:
            return FakeResponse({"url_pattern": "/hatrac/{{{md5}}}", "md5": "md5"})
        if path.startswith("/entity/PDB:entry/RCB="):
            terms = dict(t.split("=", 1) for t in path.split("/")[-1].split("&"))
            terms.pop("RCB")
            return FakeResponse([e for e in self.existing
                                 if all(urlunquote(e[c]) == urlunquote(v)
                                        for c, v in terms.items())])
        raise AssertionError("unexpected GET " + path)

    def post(self, path, json):
        table, _, named = path.partition("?defaults=")
        assert table == "/entity/PDB:entry"
        defaults = named.split(",") if named else []
        url = "https://h/ermrest/catalog/99" + path
        if any(name not in self.columns for name in defaults):
            raise refusal(409, url)
        (row,) = json
        supplied = [name for name in self.columns if name not in defaults]
        if not all(self.columns.get(name) for name in supplied + list(row)):
            raise refusal(403, url)
        self.inserts.append((defaults, row))
        return FakeResponse([dict(row, RID="5-NEW")])


@pytest.fixture
def deposit(monkeypatch, tmp_path):
    """Run do_upload against an EntryCatalog; the file transfers are stubbed."""
    cif = tmp_path / "model.cif"
    cif.write_text("data_X\n")
    png = tmp_path / "model.png"
    png.write_bytes(b"png")
    uploads = []

    def run(columns, image=False, existing=(), force=False, image_path=None):
        catalog = EntryCatalog(columns, existing)
        monkeypatch.setattr(ihmdep, "connect", lambda: (catalog, "store"))
        monkeypatch.setattr(ihmdep, "prepare_asset",
                            lambda path, *a, **k: {"path": path, "name": os.path.basename(path),
                                                   "md5": "md5of" + path[-3:]})

        def put(_s, prepared, *a, **k):
            uploads.append(prepared["path"])
            return {"URL": "/hatrac/" + prepared["md5"], "Name": "n",
                    "MD5": prepared["md5"], "Bytes": 7}
        monkeypatch.setattr(ihmdep, "put_asset", put)
        picture = image_path or (str(png) if image else None)
        args = types.SimpleNamespace(file=str(cif), ihmcif=None, force=force, draft=False,
                                     image=picture, method="Integrative modeling")
        return ihmdep.do_upload(args), catalog

    run.uploads = uploads
    return run


def test_upload_survives_a_column_only_the_backend_may_fill(deposit):
    rid, catalog = deposit(DEV)
    assert rid == "5-NEW"
    assert len(catalog.inserts) == 1


def test_upload_names_no_default_the_table_does_not_have(deposit):
    rid, catalog = deposit(PRODUCTION)
    assert rid == "5-NEW"


def test_upload_supplies_the_file_and_leaves_the_rest_to_the_server(deposit):
    _, catalog = deposit(DEV)
    (defaults, row), = catalog.inserts
    assert row == {"mmCIF_File_URL": "/hatrac/md5ofcif", "mmCIF_File_Name": "n",
                   "mmCIF_File_MD5": "md5ofcif", "mmCIF_File_Bytes": 7,
                   "Method_Details": "Integrative modeling", "Workflow_Status": "DEPO"}
    assert sorted(defaults) == sorted(set(DEV) - set(row))


def test_upload_with_an_image_supplies_the_image_columns(deposit):
    _, catalog = deposit(DEV, image=True)
    (defaults, row), = catalog.inserts
    assert row["Image_File_URL"] == "/hatrac/md5ofpng"
    assert not {"Image_File_URL", "Image_File_Name", "Image_File_MD5",
                "Image_File_Bytes"} & set(defaults)


def entry(rid="5-OLD", md5="md5ofcif", name="model.cif", image_md5=None, image_name=None):
    """One of the depositor's entries, as /entity returns it."""
    placeholder_name, placeholder_md5 = ihmdep.PLACEHOLDER_IMAGE
    return {"RID": rid, "Workflow_Status": "RECORD READY", "Process_Status": "Success",
            "mmCIF_File_MD5": md5, "mmCIF_File_Name": name,
            "Image_File_MD5": image_md5 or placeholder_md5,
            "Image_File_Name": image_name or placeholder_name}


def test_upload_of_a_deposited_file_says_what_became_of_the_image(deposit, capsys):
    """A re-run with --image used to drop the image without a word."""
    rid, catalog = deposit(PRODUCTION, image=True, existing=[entry()])
    assert rid == "5-OLD"
    err = capsys.readouterr().err
    assert "The image was not added" in err
    assert "ihmdep update 5-OLD --image " in err and "--force" not in err
    assert catalog.inserts == [] and deposit.uploads == []


def test_rerun_with_the_image_already_there_says_nothing_about_it(deposit, capsys):
    """The README's own batch loop re-runs exactly like this."""
    rid, _ = deposit(PRODUCTION, image=True,
                     existing=[entry(image_md5="md5ofpng", image_name="model.png")])
    assert rid == "5-OLD"
    assert "image" not in capsys.readouterr().err


def test_rerun_hint_over_a_different_image_needs_force(deposit, capsys):
    deposit(PRODUCTION, image=True, existing=[entry(image_md5="other", image_name="old.png")])
    err = capsys.readouterr().err
    assert "replace" in err and "--force" in err


def test_rerun_hint_quotes_the_image_path(deposit, capsys, tmp_path):
    odd = tmp_path / "image (35).png"
    odd.write_bytes(b"png")
    deposit(PRODUCTION, image_path=str(odd), existing=[entry()])
    assert "--image '%s'" % odd in capsys.readouterr().err


def test_force_cannot_deposit_the_same_file_twice(deposit, capsys):
    """The server keeps one entry per file: --force used to upload and then 409."""
    rid, catalog = deposit(PRODUCTION, force=True, existing=[entry()])
    assert rid == "5-OLD"
    assert catalog.inserts == [] and deposit.uploads == []
    assert "--salt" in capsys.readouterr().err


def test_same_name_different_file_is_refused_before_uploading(deposit):
    """One entry per file name too: refuse with the entry, not a bare 409."""
    with pytest.raises(SystemExit) as caught:
        deposit(PRODUCTION, existing=[entry(md5="an-older-version")])
    assert "5-OLD" in str(caught.value) and "rename" in str(caught.value)
    assert deposit.uploads == [], "nothing may reach Hatrac first"


def test_run_refuses_draft_before_depositing(monkeypatch):
    monkeypatch.setattr(ihmdep, "do_upload", lambda args: pytest.fail("uploaded"))
    with pytest.raises(SystemExit) as caught:
        ihmdep.do_run(types.SimpleNamespace(draft=True))
    assert "upload --draft" in str(caught.value)
