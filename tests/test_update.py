"""Offline tests for `ihmdep update`: adding an image to an existing entry.

The fake stands in for the catalog. Like the server, it answers "may this
client change these columns of this row" through tcrs(), and it records the
row update, so these tests see exactly what ihmdep would write.
"""

import types

import pytest

from ihmtools import ihmdep

PLACEHOLDER = {"Image_File_Name": "missing_image.png",
               "Image_File_MD5": "27994cb72f6d4780d9b09a85b9b5ac50"}
OWN_IMAGE = {"Image_File_Name": "figure1.png", "Image_File_MD5": "0123456789abcdef" * 2}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class EntryCatalog:
    def __init__(self, entry, may_update=True):
        self.entry = entry
        self.may_update = may_update
        self.updates = []

    def get(self, path):
        if "/annotation/" in path:
            return FakeResponse({"url_pattern": "/hatrac/{{{md5}}}", "md5": "md5"})
        if path.startswith("/attributegroup/PDB:entry/RID="):
            assert ";tcrs:=tcrs(RID)" in path
            if self.entry is None or "RID=%s/" % self.entry["RID"] not in path:
                return FakeResponse([])
            rights = {c: self.may_update for c in ihmdep.IMAGE_COLUMNS}
            return FakeResponse([dict(self.entry, tcrs={
                "update": self.may_update, "column_update": rights})])
        raise AssertionError("unexpected GET " + path)

    def put(self, path, json):
        self.updates.append((path, json))
        return FakeResponse(json)


def entry(image, workflow="RECORD READY", process="Success"):
    return dict(image, RID="5-ABCD", RCB="https://auth.globus.org/OWNER",
                Workflow_Status=workflow, Process_Status=process)


@pytest.fixture
def update(monkeypatch, tmp_path):
    """Run do_update against an EntryCatalog; the file transfer is stubbed."""
    png = tmp_path / "model.png"
    png.write_bytes(b"\x89PNG not really")
    sent = []

    def run(catalog, rid="5-ABCD", image=True, force=False):
        monkeypatch.setattr(ihmdep, "connect", lambda: (catalog, "store"))

        def put_asset(_store, prepared, _asset, uid):
            sent.append((prepared["name"], uid))
            return {"URL": "/hatrac/" + prepared["md5"], "Name": prepared["name"],
                    "MD5": prepared["md5"], "Bytes": prepared["size"]}
        monkeypatch.setattr(ihmdep, "put_asset", put_asset)
        args = types.SimpleNamespace(rid=rid, image=str(png) if image else None,
                                     force=force)
        return ihmdep.do_update(args)

    run.sent = sent
    run.png = png
    return run


def test_update_replaces_the_placeholder(update, capsys):
    catalog = EntryCatalog(entry(PLACEHOLDER))
    assert update(catalog) == "5-ABCD"
    (path, rows), = catalog.updates
    assert path == ("/attributegroup/PDB:entry/RID;"
                    "Image_File_URL,Image_File_Name,Image_File_MD5,Image_File_Bytes")
    (row,) = rows
    assert row["RID"] == "5-ABCD" and row["Image_File_Name"] == "model.png"
    assert row["Image_File_Bytes"] == len(update.png.read_bytes())
    # Only the image columns: nothing that would make the server reprocess.
    assert set(row) == {"RID"} | set(ihmdep.IMAGE_COLUMNS)
    # The file goes under the entry creator's path, as the url_pattern says.
    assert update.sent == [("model.png", "OWNER")]
    assert capsys.readouterr().out == "5-ABCD\n"


def test_update_keeps_an_image_the_entry_already_has(update):
    catalog = EntryCatalog(entry(OWN_IMAGE))
    with pytest.raises(SystemExit) as e:
        update(catalog)
    assert "already has an image (figure1.png)" in str(e.value)
    assert "--force" in str(e.value)
    assert catalog.updates == [] and update.sent == []


def test_update_force_replaces_an_image(update):
    catalog = EntryCatalog(entry(OWN_IMAGE))
    assert update(catalog, force=True) == "5-ABCD"
    assert len(catalog.updates) == 1


def test_update_leaves_alone_what_the_server_would_refuse(update):
    catalog = EntryCatalog(entry(PLACEHOLDER, workflow="SUBMIT"), may_update=False)
    with pytest.raises(SystemExit) as e:
        update(catalog)
    assert "5-ABCD is SUBMIT / Success" in str(e.value)
    # Refused before anything was sent, file or row.
    assert catalog.updates == [] and update.sent == []


def test_update_unknown_rid(update, capsys):
    catalog = EntryCatalog(entry(PLACEHOLDER))
    with pytest.raises(SystemExit) as e:
        update(catalog, rid="5-NONE")
    assert e.value.code == 3
    assert "5-NONE: unknown RID" in capsys.readouterr().err
    assert catalog.updates == []


def test_update_with_the_same_image_writes_nothing(update, capsys):
    md5 = ihmdep.common.file_digest(str(update.png))[0]
    catalog = EntryCatalog(entry({"Image_File_Name": "model.png", "Image_File_MD5": md5}))
    assert update(catalog) == "5-ABCD"
    assert catalog.updates == [] and update.sent == []
    assert "already has this image" in capsys.readouterr().err


def test_update_needs_something_to_update(update):
    catalog = EntryCatalog(entry(PLACEHOLDER))
    with pytest.raises(SystemExit) as e:
        update(catalog, image=False)
    assert "nothing to update" in str(e.value)


def test_update_checks_the_file_before_the_entry(monkeypatch, tmp_path):
    catalog = EntryCatalog(entry(PLACEHOLDER))
    monkeypatch.setattr(ihmdep, "connect", lambda: (catalog, "store"))
    gif = tmp_path / "model.gif"
    gif.write_bytes(b"GIF89a")
    with pytest.raises(SystemExit) as e:
        ihmdep.do_update(types.SimpleNamespace(rid="5-ABCD", image=str(gif), force=False))
    assert ".png" in str(e.value) and ".jpg" in str(e.value)
    assert catalog.updates == []


@pytest.mark.parametrize("image, expected", [
    (PLACEHOLDER, False),
    ({"Image_File_Name": "missing_image.png", "Image_File_MD5": "other"}, False),
    ({"Image_File_Name": "x.png", "Image_File_MD5": PLACEHOLDER["Image_File_MD5"]}, False),
    ({"Image_File_Name": None, "Image_File_MD5": None}, False),
    (OWN_IMAGE, True),
])
def test_has_image(image, expected):
    assert ihmdep.has_image(image) is expected


def test_update_parses(monkeypatch):
    seen = {}
    monkeypatch.setattr(ihmdep, "do_update", lambda args: seen.update(vars(args)))
    monkeypatch.setattr(ihmdep.sys, "argv",
                        ["ihmdep", "--mode", "dev", "update", "5-ABCD", "--image", "x.png", "-f"])
    ihmdep.main()
    assert (seen["rid"], seen["image"], seen["force"]) == ("5-ABCD", "x.png", True)
