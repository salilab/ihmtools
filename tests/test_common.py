"""Tests for ihmtools._common -- the layer both CLIs sit on.

Everything here used to exist twice, once per front end. It is tested once
now, which is the point of the module.
"""

import io
import pathlib
import struct
import sys
import types
import zlib

import pytest
import requests

from ihmtools import _common as common

MODES = {"dev": ("dev.example", "1"), "production": ("prod.example", "2")}


def aim_at(mode="dev", **kw):
    """configure() is parameterised by the front end; give it a stand-in."""
    common.configure(types.SimpleNamespace(mode=mode, **kw), MODES, "TEST",
                     default_mode="dev")


# --------------------------------------------------------------------------
# hatrac url_pattern resolution -- the one thing deriva-py cannot do for us
# --------------------------------------------------------------------------

ASSET = {
    "md5": "File_MD5",
    "url_pattern": (
        '/hatrac/dev/common/submitted/uid/{{#if _RCB}}'
        '{{#regexFindFirst _RCB "[^/]+$"}}{{this}}{{/regexFindFirst}}{{else}}'
        '{{#regexFindFirst $session.client.id "[^/]+$"}}{{this}}{{/regexFindFirst}}'
        '{{/if}}/structure/mmCIF/{{{File_MD5}}}{{{_File_URL.filename_ext}}}'
    ),
}


def test_hatrac_target_resolves():
    got = common.hatrac_target(ASSET, "UID", "MD5", ".cif")
    assert got == "/hatrac/dev/common/submitted/uid/UID/structure/mmCIF/MD5.cif"
    assert "{{" not in got


def test_hatrac_target_refuses_unknown_field():
    """Guessing a path would put the file where the pipeline never looks."""
    with pytest.raises(SystemExit):
        common.hatrac_target({"md5": "File_MD5",
                              "url_pattern": "/hatrac/x/{{{Some_New_Field}}}"},
                             "UID", "MD5", ".cif")


def args_with(rids=None, rid_flags=None):
    return types.SimpleNamespace(rids=rids or [], rid_flags=rid_flags)


def test_collect_rids_merges_and_dedupes():
    got = common.collect_rids(args_with(["B", "A"], ["C", "A"]))
    assert got == ["B", "A", "C"]


def test_collect_rids_empty_means_list():
    assert common.collect_rids(args_with()) == []


def test_collect_rids_reads_stdin_only_for_dash(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("X\nY\n"))
    assert common.collect_rids(args_with(["-"])) == ["X", "Y"]


def test_collect_rids_dash_twice_does_not_reread(monkeypatch):
    """A second '-' would otherwise block on an already-exhausted stdin."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("X\n"))
    assert common.collect_rids(args_with(["-", "-"])) == ["X"]


def test_collect_rids_dash_mixes_with_flags(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("X\n"))
    assert common.collect_rids(args_with(["-"], ["Y"])) == ["X", "Y"]


def test_collect_rids_empty_stdin_is_an_error(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit):
        common.collect_rids(args_with(["-"]))


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
        common.emit_table(headers, rows, **kw)
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
    common.emit_table(["RID"], [["A"]], notes=["the log"])
    captured = capsys.readouterr()
    assert "the log" not in captured.out, "stdout must stay parseable"
    assert "the log" in captured.err


def test_details_block_unescapes_literal_newlines():
    """The catalog stores logs with backslash-n rather than real newlines."""
    assert common.details_block("a\\nb", indent="") == "a\nb"


class FakeGlobus:
    """Stands in for GlobusNativeLogin, recording what logout would have done."""

    def __init__(self, logged_in=True):
        self._logged_in = logged_in
        self.revoked = []
        self.cleared = 0
        self.client = types.SimpleNamespace(
            token_storage=types.SimpleNamespace(clear_tokens=self._clear))

    def _clear(self, *_a, **_kw):
        self.cleared += 1

    def is_logged_in(self, hosts=()):
        return self._logged_in

    def logout(self, hosts=()):
        self.revoked.extend(hosts)


def test_logout_revokes_at_globus(monkeypatch, capsys):
    fake = FakeGlobus()
    monkeypatch.setattr(common, "_globus", lambda: fake)
    aim_at()

    common.do_logout(types.SimpleNamespace(local=False))
    assert fake.revoked == [common.HOST], "logout must revoke, not just forget"
    assert "revoked" in capsys.readouterr().out


def test_logout_local_does_not_contact_globus(monkeypatch):
    fake = FakeGlobus()
    monkeypatch.setattr(common, "_globus", lambda: fake)
    aim_at()

    common.do_logout(types.SimpleNamespace(local=True))
    assert fake.revoked == [], "--local must not reach the network"
    assert fake.cleared == 1


def test_logout_when_not_logged_in(monkeypatch, capsys):
    fake = FakeGlobus(logged_in=False)
    monkeypatch.setattr(common, "_globus", lambda: fake)
    aim_at()

    common.do_logout(types.SimpleNamespace(local=False))
    assert fake.revoked == [] and fake.cleared == 0
    assert "not logged in" in capsys.readouterr().err


LEGACY = {"access_token": "a", "refresh_token": "r", "expires_at_seconds": 9e11,
          "resource_server": "usc_isrd", "token_type": "Bearer",
          "scope": "https://auth.globus.org/scopes/x/deriva_all",
          # our old format carried two fields the token store rejects
          "expires_in": 172800, "state": "_default"}


def adoption(tmp_path, monkeypatch, legacy=LEGACY, deriva_exists=False):
    import json as _json
    old = tmp_path / "legacy.json"
    new = tmp_path / "globus-credential.json"
    if legacy is not None:
        old.write_text(_json.dumps(legacy))
    if deriva_exists:
        new.write_text("{}")
    monkeypatch.setattr(common, "LEGACY_TOKENS", str(old))
    monkeypatch.setattr(common, "DEFAULT_GLOBUS_CREDENTIAL_FILE", str(new))
    monkeypatch.setattr(common, "DerivaJSONTokenStorage", lambda *a, **kw: _Storage(new))
    return old, new


class _Storage:
    def __init__(self, path):
        self.path = path

    def write_tokens(self, tokens, overwrite=False):
        import json as _json
        self.path.write_text(_json.dumps(tokens))


def test_adopts_legacy_token(tmp_path, monkeypatch, capsys):
    """Upgrading must not silently log people out: same client, same grant."""
    import json as _json
    _old, new = adoption(tmp_path, monkeypatch)
    common.adopt_legacy_tokens()

    written = _json.loads(new.read_text())["usc_isrd"]
    assert written["access_token"] == "a"
    # expires_in and state make the store reject the whole group
    assert set(written) <= common.TOKEN_GROUP_KEYS
    assert "Imported credentials" in capsys.readouterr().err


def test_adoption_does_not_overwrite_derivas_store(tmp_path, monkeypatch):
    _old, new = adoption(tmp_path, monkeypatch, deriva_exists=True)
    common.adopt_legacy_tokens()
    assert new.read_text() == "{}", "an existing deriva login wins"


def test_adoption_skips_an_incomplete_token(tmp_path, monkeypatch):
    partial = {k: v for k, v in LEGACY.items() if k != "access_token"}
    _old, new = adoption(tmp_path, monkeypatch, legacy=partial)
    common.adopt_legacy_tokens()
    assert not new.exists()


def test_adoption_survives_a_corrupt_file(tmp_path, monkeypatch):
    old = tmp_path / "legacy.json"
    old.write_text("not json at all")
    monkeypatch.setattr(common, "LEGACY_TOKENS", str(old))
    monkeypatch.setattr(common, "DEFAULT_GLOBUS_CREDENTIAL_FILE",
                        str(tmp_path / "absent.json"))
    common.adopt_legacy_tokens()          # must not raise


# --------------------------------------------------------------------------
# broken pipe -- two distinct failure paths, both handled by dispatch()
# --------------------------------------------------------------------------

def dispatch_with(stdout, fn):
    """Run one command through dispatch() with stdout replaced."""
    real = sys.stdout
    sys.stdout = stdout
    try:
        with pytest.raises(SystemExit) as caught:
            common.dispatch(types.SimpleNamespace(func=fn))
        return caught.value.code
    finally:
        sys.stdout = real


class ClosedPipe(io.StringIO):
    """Accepts writes, then fails on flush -- the `| head` case."""

    def __init__(self, raise_on_write=False):
        super().__init__()
        self._on_write = raise_on_write

    def write(self, text):
        if self._on_write:
            raise BrokenPipeError(32, "Broken pipe")
        return 0                      # buffered, no error yet

    def flush(self):
        raise BrokenPipeError(32, "Broken pipe")

    def isatty(self):
        return False


def test_broken_pipe_during_execution_is_not_a_traceback():
    """Output past the 8 KB buffer raises from print() while the command runs."""
    def writes(_args):
        print("x")
    assert dispatch_with(ClosedPipe(raise_on_write=True), writes) == 141


def test_broken_pipe_while_flushing_is_not_a_traceback():
    """Output that fits the buffer raises nothing until the interpreter flushes
    at shutdown, where no handler can run -- so dispatch() must flush itself."""
    assert dispatch_with(ClosedPipe(), lambda _args: None) == 141


@pytest.mark.parametrize("name", ["ihmv", "ihmdep"])
def test_each_front_end_routes_through_dispatch(monkeypatch, name):
    """Guards the wiring: a main() that called args.func directly would let the
    broken-pipe traceback back in, and nothing else here would notice."""
    import importlib
    mod = importlib.import_module("ihmtools." + name)
    monkeypatch.setattr(mod, "do_status", lambda _args: None)
    monkeypatch.setattr(sys, "argv", [name, "get_status"])
    monkeypatch.setattr(sys, "stdout", ClosedPipe())

    with pytest.raises(SystemExit) as caught:
        mod.main()
    assert caught.value.code == 141


# --------------------------------------------------------------------------
# salting -- making an already-deposited file new again
# --------------------------------------------------------------------------

CIF = b"data_TEST\n_struct.entry_id TEST\n"


def png_chunk(kind, body=b""):
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff))


PNG = (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", b"\0" * 13) + png_chunk(b"IEND"))


def test_salt_mmcif_is_a_comment():
    """A trailing '#' comment is core CIF syntax and touches no data item."""
    out = common.salt_mmcif(CIF, "S")
    assert out.startswith(CIF.rstrip(b"\n"))
    assert out.splitlines()[-1] == b"# ihmtools-salt: S"


def test_salt_mmcif_changes_the_hash():
    assert common.salt_mmcif(CIF, "A") != common.salt_mmcif(CIF, "B")


def test_salt_png_keeps_every_chunk_valid():
    """Appending past IEND would leave validity to the decoder; this inserts."""
    out = common.salt_png(PNG, "S")
    assert out.startswith(b"\x89PNG\r\n\x1a\n")
    off, seen = 8, []
    while off < len(out):
        length, kind = struct.unpack(">I4s", out[off:off + 8])
        body = out[off + 8:off + 8 + length]
        crc, = struct.unpack(">I", out[off + 8 + length:off + 12 + length])
        assert zlib.crc32(kind + body) & 0xffffffff == crc, "%s has a bad CRC" % kind
        seen.append(kind)
        off += 12 + length
    assert seen == [b"IHDR", b"tEXt", b"IEND"], "the chunk must land before IEND"
    assert off == len(out), "no trailing bytes past IEND"


def test_salted_renames_and_rewrites(tmp_path):
    src = tmp_path / "model.cif"
    src.write_bytes(CIF)
    out = pathlib.Path(common.salted(str(src), "20260101000000", str(tmp_path)))

    assert out.name == "model_20260101000000.cif"
    assert out.read_bytes() != CIF
    assert src.read_bytes() == CIF, "the original must not be touched"


def test_salted_refuses_a_format_it_cannot_salt(tmp_path):
    """Silently uploading an unsalted file would look like success."""
    src = tmp_path / "notes.txt"
    src.write_bytes(b"hello")
    with pytest.raises(SystemExit):
        common.salted(str(src), "S", str(tmp_path))


def test_apply_salt_is_a_no_op_without_the_flag():
    args = types.SimpleNamespace(salt=False)
    assert common.apply_salt(args, "a.cif", None) == ("a.cif", None)


def test_apply_salt_leaves_a_missing_optional_alone(tmp_path, capsys):
    """ihmdep passes (mmcif, image) and the image is usually absent."""
    src = tmp_path / "model.cif"
    src.write_bytes(CIF)
    cif, image = common.apply_salt(types.SimpleNamespace(salt=True), str(src), None)

    assert image is None
    assert pathlib.Path(cif).read_bytes() != CIF
    assert "salted with" in capsys.readouterr().err


# --------------------------------------------------------------------------
# identity -- a 403 is more often the wrong account than a missing grant
# --------------------------------------------------------------------------

CLIENT = {"id": "https://auth.globus.org/442a6439-c239",
          "display_name": "someone@example.org",
          "full_name": "Some One", "email": "someone@example.org"}


class FakeClient:
    """A deriva-py client object: the thing a bound method hangs off."""

    def __init__(self, session=None, boom=False):
        self.session = session or {"client": CLIENT, "attributes": []}
        self.boom = boom
        self.calls = 0

    def get_authn_session(self):
        self.calls += 1
        if self.boom:
            raise requests.ConnectionError("no route")
        return types.SimpleNamespace(json=lambda: self.session)

    def denied(self, *_a, **_kw):
        response = requests.Response()
        response.status_code = 403
        response.url = "https://h/ermrest/catalog/1/entity/PDB:entry"
        raise requests.HTTPError(response=response)


def test_describe_names_the_account_and_the_uid():
    assert common.describe(CLIENT) == "someone@example.org (442a6439-c239)"


def test_describe_survives_an_unknown_identity():
    assert common.describe(None) == "the stored credentials"


def test_403_names_the_identity_and_the_url(monkeypatch):
    """The failure that actually happens: logged in as the wrong account."""
    monkeypatch.setattr(common, "IDENTITY", None)
    monkeypatch.setattr(sys, "argv", ["ihmv"])
    client = FakeClient()

    with pytest.raises(SystemExit) as caught:
        common.check(client.denied)
    message = str(caught.value)

    assert "someone@example.org (442a6439-c239)" in message
    assert "ermrest/catalog/1/entity/PDB:entry" in message
    assert "ihmv logout" in message, "the fix has to be in the message"


def test_403_still_reports_when_the_identity_cannot_be_fetched(monkeypatch):
    """Diagnosis is best effort; it must not replace the error with its own."""
    monkeypatch.setattr(common, "IDENTITY", None)
    client = FakeClient(boom=True)

    with pytest.raises(SystemExit) as caught:
        common.check(client.denied)
    assert "the stored credentials" in str(caught.value)


def test_identity_is_fetched_once(monkeypatch):
    monkeypatch.setattr(common, "IDENTITY", None)
    client = FakeClient()
    assert common.identify(client) == CLIENT
    assert common.identify(client) == CLIENT
    assert client.calls == 1, "a cached identity must not be re-fetched"


def test_whoami_puts_only_the_identity_on_stdout(monkeypatch, capsys):
    """So `WHO=$(ihmv whoami)` works even with -v."""
    session = {"client": CLIENT,
               "attributes": [{"id": CLIENT["id"], "display_name": "someone@example.org"},
                              {"id": "https://auth.globus.org/g1", "display_name": "pdb-writer"},
                              {"id": "https://auth.globus.org/g2", "display_name": None}]}
    monkeypatch.setattr(common, "connect", lambda: (FakeClient(session), None))
    common.do_whoami(types.SimpleNamespace(verbose=True))

    captured = capsys.readouterr()
    assert captured.out.strip() == "someone@example.org (442a6439-c239)"
    # the identity appears among its own attributes; only real groups listed
    assert "pdb-writer" in captured.err
    assert "someone@example.org" not in captured.err.split("groups")[-1]


# --------------------------------------------------------------------------
# login stays in the terminal
# --------------------------------------------------------------------------

class RecordingGlobus(FakeGlobus):
    def __init__(self):
        super().__init__()
        self.login_kwargs = None

    def login(self, **kwargs):
        self.login_kwargs = kwargs


def test_login_reports_the_identity_it_landed_on(monkeypatch, capsys):
    """The pasted code says nothing about which account it was, so say it."""
    fake = RecordingGlobus()
    monkeypatch.setattr(common, "_globus", lambda: fake)
    monkeypatch.setattr(common, "connect", lambda: (FakeClient(), None))
    aim_at()

    common.do_login(types.SimpleNamespace(browser=False))
    captured = capsys.readouterr()
    assert captured.out.strip() == "someone@example.org (442a6439-c239)"
    assert "server" in captured.err and "groups" in captured.err
    assert "tokens" in captured.err, "say where the credentials landed"


@pytest.mark.parametrize("browser, no_browser", [(False, True), (True, False)])
def test_login_does_not_open_a_browser_unless_asked(monkeypatch, browser, no_browser):
    """deriva-py opens one by default. Whichever browser it picks may be signed
    in as a different account, which is how the wrong identity gets stored."""
    fake = RecordingGlobus()
    monkeypatch.setattr(common, "_globus", lambda: fake)
    monkeypatch.setattr(common, "connect", lambda: (FakeClient(), None))
    aim_at()

    common.do_login(types.SimpleNamespace(browser=browser))
    assert fake.login_kwargs["no_browser"] is no_browser


def test_login_never_starts_a_local_server(monkeypatch):
    """A redirect to localhost cannot work over ssh or from a container."""
    fake = RecordingGlobus()
    monkeypatch.setattr(common, "_globus", lambda: fake)
    monkeypatch.setattr(common, "connect", lambda: (FakeClient(), None))
    aim_at()

    common.do_login(types.SimpleNamespace(browser=False))
    assert fake.login_kwargs["no_local_server"] is True
    assert fake.login_kwargs["refresh_tokens"] is True


# --------------------------------------------------------------------------
# batching -- a disjunction is one request, but the URL has a ceiling
# --------------------------------------------------------------------------

def batched_urls(monkeypatch, n, batch=common.BATCH):
    urls = []
    monkeypatch.setattr(common, "get", lambda _c, path: urls.append(path) or [{"n": path}])
    rows = common.get_batched(None, "/attribute/T/RID=", ["R%d" % i for i in range(n)],
                              "/RID", batch=batch)
    return urls, rows


def test_one_request_when_it_fits(monkeypatch):
    urls, rows = batched_urls(monkeypatch, 25)
    assert len(urls) == 1
    assert urls[0].startswith("/attribute/T/RID=any(R0,R1,")
    assert urls[0].endswith(")/RID")
    assert len(rows) == 1


def test_splits_past_the_batch_size(monkeypatch):
    """A 500-RID disjunction comes back 404 -- which reads as 'no such
    entries' rather than 'too long', so it must never be sent."""
    urls, rows = batched_urls(monkeypatch, 250)
    assert len(urls) == 3, "250 RIDs at 100 per request"
    assert len(rows) == 3, "every chunk's rows are kept"
    assert "any(R0," in urls[0] and "any(R100," in urls[1] and "any(R200," in urls[2]


def test_every_rid_appears_exactly_once(monkeypatch):
    urls, _ = batched_urls(monkeypatch, 250)
    seen = [r for u in urls for r in u.split("any(")[1].rstrip(")/RID").split(",")]
    assert seen == ["R%d" % i for i in range(250)]


def test_no_request_for_an_empty_list(monkeypatch):
    urls, rows = batched_urls(monkeypatch, 0)
    assert urls == [] and rows == []


def test_batch_size_stays_under_the_url_ceiling():
    """Measured: ~9 bytes per RID, 404 at ~4 KB, 414 at ~9 KB."""
    longest = common.any_of(["9-ABCDEFG"] * common.BATCH)
    assert len(longest) < 2000, "a full batch must leave room for host and columns"


def test_both_front_ends_share_one_default_mode():
    """They drifted once -- ihmv sat on dev for a week after ihmdep moved --
    because each declared its own. Neither may declare one again."""
    import importlib
    for name in ("ihmv", "ihmdep"):
        mod = importlib.import_module("ihmtools." + name)
        assert not hasattr(mod, "DEFAULT_MODE"), \
            "%s re-declares DEFAULT_MODE; it belongs to _common" % name
        assert common.DEFAULT_MODE in mod.MODES, \
            "%s has no %s mode" % (name, common.DEFAULT_MODE)


@pytest.mark.parametrize("name, host, catalog", [
    ("ihmv", "data.pdb-ihm.org", "101"),
    ("ihmdep", "data.pdb-ihm.org", "1"),
])
def test_a_bare_invocation_targets_production(monkeypatch, name, host, catalog):
    import importlib
    mod = importlib.import_module("ihmtools." + name)
    monkeypatch.delenv("%s_HOST" % name.upper(), raising=False)
    monkeypatch.delenv("%s_CATALOG" % name.upper(), raising=False)

    mod.configure(types.SimpleNamespace())
    assert (common.HOST, common.CATALOG_ID) == (host, catalog)
