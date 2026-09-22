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

Nothing is cleaned up. Each run leaves one record per tool on dev, and both
RIDs are printed. Deleting would mean a test that destroys the very evidence
you would want when it fails -- and the deposition entry could not be removed
anyway, since the round trip ends in SUBMIT and the CLI refuses past DEPO.
"""

import os
import pathlib
import subprocess
import sys
import time
import types

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
def salt():
    """One value per run, shared by every generated file."""
    return time.strftime("%Y%m%d%H%M%S")


@pytest.fixture(scope="module")
def salt_dir(tmp_path_factory):
    return str(tmp_path_factory.mktemp("salted"))


@pytest.fixture(scope="module")
def unique_cif(salt, salt_dir):
    """This run's own copy of a real entry, via the same code `--salt` uses."""
    return pathlib.Path(common.salted(TEMPLATE, salt, salt_dir))


@pytest.fixture(scope="module")
def unique_png(salt, salt_dir):
    """Likewise for the preview image -- Hatrac is content-addressed, so an
    unsalted copy would resolve to the object a previous run uploaded and the
    transfer would be skipped on the md5 match."""
    return pathlib.Path(common.salted(TEMPLATE_IMAGE, salt, salt_dir))


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
    print("ihmv submitted %s (left on dev)" % rid)

    run("ihmv", "get_status", "--wait", "--interval", POLL, rid,
        timeout=WAIT_TIMEOUT, expect=0)
    assert run("ihmv", "get_status", rid).stdout.strip() == "Success"

    out = tmp_path / "reports"
    run("ihmv", "download", rid, "-o", out)
    got = sorted(p.name for p in out.glob("*.pdf"))
    assert len(got) == 2, "expected the full and summary reports, got %s" % got
    for pdf in out.glob("*.pdf"):
        assert pdf.read_bytes()[:4] == b"%PDF", "%s is not a PDF" % pdf.name
