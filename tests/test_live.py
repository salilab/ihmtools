"""Round trips against a real catalog, driving the CLIs the way a user does.

Deselected by default -- pyproject sets `-m 'not live'`, because these need a
login and they write records. To run them:

    pytest -m live                  # both tools, against the dev servers
    pytest -m live -k ihmdep        # just the deposition round trip

Only ever dev: MODE below is a constant, and `--mode` beats IHMV_HOST /
IHMDEP_HOST in the CLIs' own precedence, so these cannot be aimed at
production by the environment.

Each run works on its own stamped copies of examples/G_1000003/9A7U.{cif,png},
since both tools dedupe on md5 and Hatrac is content-addressed -- reusing a
file would match the previous run rather than exercise the upload.

What they leave behind. The IHMV entry is deleted on the way out unless
IHMTOOLS_LIVE_KEEP is set. The deposition entry is not: the round trip ends in
SUBMIT, and past DEPO the CLI deliberately refuses to delete, so each run
leaves one record on dev. Both RIDs are printed.
"""

import os
import struct
import subprocess
import sys
import time
import types
import zlib

import pytest

from ihmtools import _common as common
from ihmtools import ihmdep

pytestmark = pytest.mark.live

# Never production. These deposit real entries and drive real state changes.
MODE = ("--mode", "dev")

EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "examples", "G_1000003")
TEMPLATE = os.path.join(EXAMPLES, "9A7U.cif")
TEMPLATE_IMAGE = os.path.join(EXAMPLES, "9A7U.png")

# The backend takes minutes on a real entry; a hung pipeline must not hang CI.
WAIT_TIMEOUT = int(os.environ.get("IHMTOOLS_LIVE_TIMEOUT", "1800"))
POLL = os.environ.get("IHMTOOLS_LIVE_INTERVAL", "30")
KEEP = bool(os.environ.get("IHMTOOLS_LIVE_KEEP"))

# The states an entry passes through before it is submitted.
PRE_SUBMIT = ("DRAFT", "DEPO", "RECORD READY")


def run(tool, *args, **kwargs):
    """Invoke one CLI as a user would, and fail loudly with its own output."""
    timeout = kwargs.pop("timeout", 300)
    expect = kwargs.pop("expect", 0)
    cmd = [sys.executable, "-m", "ihmtools." + tool]
    cmd += list(MODE) + [str(a) for a in args]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    print("$ %s\n%s%s" % (" ".join(cmd[2:]), done.stderr, done.stdout))
    if expect is not None:
        assert done.returncode == expect, (
            "%s exited %d (wanted %d)\n%s" % (tool, done.returncode, expect, done.stderr))
    return done


def status_row(tool, rid):
    """The full listing row for one RID, as a column -> value dict.

    `get_status RID` alone prints a single word, and for a processed entry that
    word is the Process_Status -- so to see the workflow state we need the
    table, which is tab-separated whenever stdout is not a terminal.

    expect=None because the exit code is the *answer* here, not a failure: 0
    done, 1 error, 2 pending. Right after SUBMIT the entry is pending again.
    """
    out = run(tool, "get_status", "-v", rid, expect=None).stdout
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) >= 2, "expected a header and a row, got:\n%s" % out
    header = lines[0].lstrip("#").split("\t")
    return dict(zip(header, lines[1].split("\t")))


def entry_columns(rid, columns):
    """Read a deposited row back from the catalog.

    Every *action* here goes through the CLI, but no command prints the image
    columns, so verifying them means asking the catalog directly.
    """
    ihmdep.configure(types.SimpleNamespace(mode="dev"))
    catalog, _ = common.connect()
    return common.get(catalog, "/attribute/PDB:entry/RID=%s/%s"
                               % (common.urlquote(rid), columns))[0]


@pytest.fixture(scope="module")
def stamp():
    """One timestamp per run, shared by every generated file."""
    return time.strftime("%Y%m%d%H%M%S")


@pytest.fixture(scope="module")
def unique_cif(tmp_path_factory, stamp):
    """A real entry, stamped so that both its name and its md5 are new.

    Both tools dedupe on md5, so an unmodified file would match the previous
    run's record and the test would assert against that instead of depositing.
    The stamp goes in the content as well as the filename, since the filename
    alone does not reach the md5.
    """
    marker = "ihmtools live test %s" % stamp

    with open(TEMPLATE) as fh:
        text = fh.read()
    stamped = text.replace("_struct.pdbx_model_details\t.",
                           '_struct.pdbx_model_details\t"%s"' % marker, 1)
    assert stamped != text, "%s no longer has an empty _struct.pdbx_model_details" % TEMPLATE

    path = tmp_path_factory.mktemp("live") / ("ihmtools_live_%s.cif" % stamp)
    path.write_text(stamped)
    return path


def png_with_text(data, keyword, text):
    """Insert a tEXt chunk just before IEND.

    Changes the file's md5 without touching a pixel, and keeps it a valid PNG
    -- which writing one from scratch would not, and Pillow is not a
    dependency of anything here.
    """
    payload = keyword.encode("latin-1") + b"\0" + text.encode("latin-1")
    chunk = (struct.pack(">I", len(payload)) + b"tEXt" + payload
             + struct.pack(">I", zlib.crc32(b"tEXt" + payload) & 0xffffffff))
    iend = data.rindex(b"IEND") - 4         # back up over the length field
    return data[:iend] + chunk + data[iend:]


@pytest.fixture(scope="module")
def unique_png(tmp_path_factory, stamp):
    """The entry's real preview image, stamped so this run's copy is its own.

    Hatrac is content-addressed, so an unstamped image would silently reuse the
    object a previous run uploaded -- which passes, but proves nothing about
    the upload path.
    """
    with open(TEMPLATE_IMAGE, "rb") as fh:
        data = fh.read()
    stamped = png_with_text(data, "Comment", "ihmtools live test %s" % stamp)
    assert stamped.startswith(b"\x89PNG\r\n\x1a\n") and stamped.endswith(b"IEND\xaeB`\x82")

    path = tmp_path_factory.mktemp("live") / ("ihmtools_live_%s.png" % stamp)
    path.write_bytes(stamped)
    return path


def test_ihmdep_deposit_to_submit(unique_cif, unique_png, tmp_path):
    """upload (with image) -> wait -> RECORD READY -> submit -> generated mmCIF."""
    rid = run("ihmdep", "upload", unique_cif, "--image", unique_png).stdout.strip()
    assert rid, "upload printed no RID"
    print("ihmdep deposited %s (left on dev; SUBMIT cannot be undone here)" % rid)

    # The image is a second asset on the same row, and nothing downstream
    # reports it, so check it landed before moving on.
    row = entry_columns(rid, "Image_File_Name,Image_File_MD5,Image_File_Bytes")
    assert row["Image_File_Name"] == unique_png.name
    assert row["Image_File_MD5"] == common.file_digest(str(unique_png))[0]
    assert row["Image_File_Bytes"] == unique_png.stat().st_size

    # upload lands in DEPO, which is what hands the entry to the backend.
    run("ihmdep", "get_status", "--wait", "--interval", POLL, rid,
        timeout=WAIT_TIMEOUT, expect=0)

    row = status_row("ihmdep", rid)
    assert row["PROCESS"] == "Success", "backend did not finish cleanly: %s" % row
    assert row["WORKFLOW"] == "RECORD READY", (
        "SUBMIT is only legal from RECORD READY, but the entry is %s" % row["WORKFLOW"])

    # set_status re-reads after writing, but the backend picks the entry up at
    # once and carries it past SUBMIT, so assert only that it left the states
    # that precede submission -- anything beyond them is the pipeline's to pick.
    now = run("ihmdep", "set_status", rid, "--to", "SUBMIT", "--yes").stdout.strip()
    assert now not in PRE_SUBMIT, "entry is still %s after SUBMIT" % now

    # SUBMIT also hands the entry back to the backend: Process_Status returns
    # to "Resume (trigger backend process)" and every Entry_Generated_File row
    # is cleared until the rerun finishes. Downloading here finds nothing.
    run("ihmdep", "get_status", "--wait", "--interval", POLL, rid,
        timeout=WAIT_TIMEOUT, expect=0)

    out = tmp_path / "generated"
    run("ihmdep", "download", rid, "--mmcif", "-o", out)
    got = list(out.glob("*.cif"))
    assert len(got) == 1, "expected one generated mmCIF, got %s" % got
    assert got[0].read_text(errors="replace").startswith("data_"), \
        "%s is not an mmCIF datablock" % got[0].name


def test_ihmv_submit_to_reports(unique_cif, tmp_path):
    """upload -> wait -> fetch both validation reports."""
    rid = run("ihmv", "upload", unique_cif).stdout.strip()
    assert rid, "upload printed no RID"
    print("ihmv submitted %s" % rid)

    try:
        run("ihmv", "get_status", "--wait", "--interval", POLL, rid,
            timeout=WAIT_TIMEOUT, expect=0)
        assert run("ihmv", "get_status", rid).stdout.strip() == "Success"

        out = tmp_path / "reports"
        run("ihmv", "download", rid, "-o", out)
        got = sorted(p.name for p in out.glob("*.pdf"))
        assert len(got) == 2, "expected the full and summary reports, got %s" % got
        for pdf in out.glob("*.pdf"):
            assert pdf.read_bytes()[:4] == b"%PDF", "%s is not a PDF" % pdf.name
    finally:
        # IHMV records are deletable, so this one need not outlive the test.
        if not KEEP:
            # Record only: purging the Hatrac object needs permissions a
            # depositor does not have, and it is content-addressed anyway.
            run("ihmv", "delete", rid, "--yes", expect=None)
