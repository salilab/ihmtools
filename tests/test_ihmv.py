"""Offline tests for the IHMV validation CLI. No network, no credentials."""

import sys
import types

import pytest
import requests

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


@pytest.mark.parametrize("state", [None, "", "Something new"])
def test_status_code_is_done_only_on_success(state):
    """A record that exists is never "unknown RID", and only Success is done:
    an empty or unexpected status must not let a script carry on."""
    assert ihmv.status_code([("R", row(state))]) == 1


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

def deletion(monkeypatch, records, reports=(), others=(), store_refuses=False):
    """Run do_delete over fake records; returns (catalog deletes, hatrac deletes)."""
    deleted, purged = [], []
    catalog = types.SimpleNamespace(delete=lambda path, **kw: deleted.append(path))

    def del_obj(path, **kw):
        if store_refuses:       # what dev answers a submitter, owner or not
            response = requests.Response()
            response.status_code, response.url = 403, "https://h" + path
            response._content = b"delete access forbidden."
            raise requests.HTTPError(response=response)
        purged.append(path)
    store = types.SimpleNamespace(del_obj=del_obj)
    monkeypatch.setattr(ihmv, "connect", lambda: (catalog, store))

    def get(_c, path):
        if "Generated_File" in path:
            return [{"Structure_mmCIF": r} for r in reports]
        if "/File_MD5=" in path:
            return list(others)         # what still references the file
        return records
    monkeypatch.setattr(common, "get", get)     # what get_batched calls
    monkeypatch.setattr(ihmv, "get", get)
    ihmv.do_delete(types.SimpleNamespace(rids=[r["RID"] for r in records], rid_flags=None,
                                         yes=True, purge_file=True))
    return deleted, purged


def record(rid, url="/hatrac/x.cif:V1"):
    return {"RID": rid, "Title": "t", "Processing_Status": "Success",
            "File_MD5": "m", "File_URL": url}


@pytest.mark.parametrize("reports", [[], ["R"]])
def test_delete_leaves_the_reports_to_the_cascade(monkeypatch, reports):
    """Generated_File's FK cascades, and a submitter may not delete from it:
    asking to was a 403 for every record that had reports."""
    deleted, _ = deletion(monkeypatch, [record("R")], reports=reports)
    assert deleted == ["/entity/IHMV:Structure_mmCIF/RID=R"]


def test_purge_removes_the_object_not_a_version(monkeypatch):
    _, purged = deletion(monkeypatch, [record("R")])
    assert purged == ["/hatrac/x.cif"]


def test_purge_removes_a_shared_file_once(monkeypatch):
    """Two records just deleted together shared it: the second purge was a 404."""
    _, purged = deletion(monkeypatch, [record("R1"), record("R2")])
    assert purged == ["/hatrac/x.cif"]


def test_purge_keeps_a_file_another_record_uses(monkeypatch):
    _, purged = deletion(monkeypatch, [record("R")], others=[{"RID": "R9"}])
    assert purged == []


def test_purge_of_an_unversioned_url_keeps_the_whole_path(monkeypatch):
    _, purged = deletion(monkeypatch, [record("R", url="https://h:443/hatrac/x.cif")])
    assert purged == ["https://h:443/hatrac/x.cif"]


# --------------------------------------------------------------------------
# upload: the server's unique keys, checked before anything is uploaded
# --------------------------------------------------------------------------

class Answer:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class StructureCatalog:
    """IHMV:Structure_mmCIF as dev serves it: unique per submitter on the file,
    the title and the file name."""

    KEYS = [["RID"], ["RCB", "File_MD5"], ["RCB", "Title"], ["RCB", "File_Name"]]

    def __init__(self, existing=()):
        self.existing = list(existing)
        self.inserts = []

    def get_authn_session(self):
        return Answer({"client": {"id": "https://auth.globus.org/ME"}})

    def get(self, path):
        if path == ihmv.ASSET_ANNOTATION:
            return Answer({"url_pattern": "/hatrac/{{{md5}}}", "md5": "md5"})
        if path == "/schema/IHMV/table/Structure_mmCIF":
            return Answer({"keys": [{"unique_columns": k} for k in self.KEYS]})
        if path.startswith("/entity/IHMV:Structure_mmCIF/RCB="):
            terms = dict(t.split("=", 1) for t in path.split("/")[-1].split("&"))
            terms.pop("RCB")
            return Answer([e for e in self.existing
                           if all(common.urlquote(str(e[c])) == v for c, v in terms.items())])
        raise AssertionError("unexpected GET " + path)

    def post(self, path, json):
        self.inserts.append(json)
        return Answer([dict(json[0], RID="NEW")])


@pytest.fixture
def submit(monkeypatch, tmp_path):
    cif = tmp_path / "model.cif"
    cif.write_text("data_X\n")
    uploads = []

    def run(existing=(), title=None, force=False):
        catalog = StructureCatalog(existing)
        monkeypatch.setattr(ihmv, "connect", lambda: (catalog, "store"))
        monkeypatch.setattr(ihmv, "put_asset", lambda _s, prep, *a: uploads.append(prep) or {
            "URL": "/hatrac/u", "Name": prep["name"], "MD5": prep["md5"], "Bytes": 7})
        args = types.SimpleNamespace(file=str(cif), title=title, description=None,
                                     force=force, salt=False)
        return ihmv.do_upload(args), catalog

    run.uploads = uploads
    run.md5 = common.file_digest(str(cif))[0]
    return run


def mine_already(md5="other", title="t", name="other.cif"):
    return {"RID": "OLD", "File_MD5": md5, "Title": title, "File_Name": name,
            "Processing_Status": "Success"}


def test_upload_inserts_when_nothing_clashes(submit):
    rid, catalog = submit()
    assert rid == "NEW" and len(catalog.inserts) == 1


def test_upload_of_a_submitted_file_returns_its_record(submit, capsys):
    rid, catalog = submit(existing=[mine_already(md5=submit.md5)], force=True)
    assert rid == "OLD" and catalog.inserts == [] and submit.uploads == []
    assert "set_status OLD --to Reprocess" in capsys.readouterr().err


def test_upload_refuses_a_title_in_use_before_uploading(submit):
    with pytest.raises(SystemExit) as caught:
        submit(existing=[mine_already(title="mine")], title="mine")
    assert "OLD" in str(caught.value) and "--title" in str(caught.value)
    assert submit.uploads == []


def test_upload_refuses_a_file_name_in_use_where_the_server_keys_on_it(submit):
    with pytest.raises(SystemExit) as caught:
        submit(existing=[mine_already(name="model.cif")])
    assert "Rename the file" in str(caught.value)
    assert submit.uploads == []


# --------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------

def download(monkeypatch, tmp_path, records, reports):
    fetched = []
    store = types.SimpleNamespace(get_obj=lambda url, destfilename: fetched.append(destfilename))
    monkeypatch.setattr(ihmv, "connect", lambda: (None, store))
    monkeypatch.setattr(common, "get", lambda _c, path: (
        reports if "Generated_File" in path else records))
    args = types.SimpleNamespace(rids=["R", "GONE"], rid_flags=None, full=False,
                                 summary=False, mmcif=False, outdir=str(tmp_path))
    with pytest.raises(SystemExit) as caught:
        ihmv.do_download(args)
    return caught.value.code, fetched


def test_download_tells_an_unknown_rid_from_a_missing_report(monkeypatch, tmp_path, capsys):
    code, _ = download(monkeypatch, tmp_path, [{"RID": "R", "File_Name": "m.cif",
                                                "File_URL": "/u"}], [])
    assert code == 3, "a wrong RID exits 3, as get_status does"
    err = capsys.readouterr().err
    assert "GONE: unknown RID" in err and "R: nothing to download yet" in err


def test_download_never_writes_outside_the_directory(monkeypatch, tmp_path):
    report = {"Structure_mmCIF": "R", "File_Type": "Validation: Full PDF",
              "File_Name": "../../escape.pdf", "File_URL": "/u"}
    _, fetched = download(monkeypatch, tmp_path,
                          [{"RID": "R", "File_Name": "m.cif", "File_URL": "/u"}], [report])
    assert fetched == [str(tmp_path / "escape.pdf")]


def test_download_keeps_the_rid_prefix_on_a_hostile_name(monkeypatch, tmp_path):
    records = [{"RID": "R", "File_Name": "sub/m.cif", "File_URL": "/u"},
               {"RID": "GONE", "File_Name": "x.cif", "File_URL": "/v"}]
    fetched = []
    store = types.SimpleNamespace(get_obj=lambda url, destfilename: fetched.append(destfilename))
    monkeypatch.setattr(ihmv, "connect", lambda: (None, store))
    monkeypatch.setattr(common, "get", lambda _c, path: (
        [] if "Generated_File" in path else records))
    args = types.SimpleNamespace(rids=["R", "GONE"], rid_flags=None, full=False,
                                 summary=False, mmcif=True, outdir=str(tmp_path))
    ihmv.do_download(args)
    assert fetched == [str(tmp_path / "R_m.cif"), str(tmp_path / "GONE_x.cif")]


def test_a_purge_the_server_refuses_is_reported_not_fatal(monkeypatch, capsys):
    """Found on dev 199: submitters may not delete their own uploads, so the
    purge used to fail the command after the records were already gone."""
    deleted, purged = deletion(monkeypatch, [record("R")], store_refuses=True)
    assert deleted == ["/entity/IHMV:Structure_mmCIF/RID=R"] and purged == []
    assert "kept x.cif" in capsys.readouterr().err


def test_a_shared_file_is_reported_once_when_refused(monkeypatch, capsys):
    deletion(monkeypatch, [record("R1"), record("R2")], store_refuses=True)
    assert capsys.readouterr().err.count("kept x.cif") == 1
