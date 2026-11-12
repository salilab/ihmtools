"""Offline tests for `ihmdep upload`: the insert of the PDB:entry row.

The fake stands in for the catalog, not for ihmdep: it applies ERMrest's own
rule for an insert, so these tests fail when ihmdep stops telling the server
which columns to fill in by itself.
"""

import types

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

    def __init__(self, columns):
        self.columns = columns
        self.inserts = []

    def get_authn_session(self):
        return FakeResponse({"client": {"id": "https://auth.globus.org/UID"}})

    def get(self, path):
        if path == "/schema/PDB/table/entry":
            return FakeResponse({"column_definitions": [
                {"name": name, "rights": {"insert": allowed}}
                for name, allowed in self.columns.items()]})
        if "/annotation/" in path:
            return FakeResponse({"url_pattern": "/hatrac/{{{md5}}}", "md5": "md5"})
        if path.startswith("/entity/PDB:entry/mmCIF_File_MD5="):
            return FakeResponse([])            # nothing deposited yet
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

    def run(columns, image=False):
        catalog = EntryCatalog(columns)
        monkeypatch.setattr(ihmdep, "connect", lambda: (catalog, "store"))
        monkeypatch.setattr(ihmdep, "prepare_asset",
                            lambda path, *a, **k: {"path": path, "md5": "md5of" + path[-3:]})
        monkeypatch.setattr(ihmdep, "put_asset",
                            lambda _s, prepared, *a, **k: {
                                "URL": "/hatrac/" + prepared["md5"], "Name": "n",
                                "MD5": prepared["md5"], "Bytes": 7})
        args = types.SimpleNamespace(file=str(cif), ihmcif=None, force=False, draft=False,
                                     image=str(png) if image else None,
                                     method="Integrative modeling")
        return ihmdep.do_upload(args), catalog

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
