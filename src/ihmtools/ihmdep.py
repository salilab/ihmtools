"""Minimal CLI for the PDB-IHM deposition system.

Built on deriva-py: ErmrestCatalog for records, HatracStore for files, and
GlobusNativeLogin for authentication.

    ihmdep login                          authenticate with Globus (once)
    ihmdep whoami                         which account you are logged in as
    ihmdep upload model.cif               deposit an entry
    ihmdep upload model.cif --image f.png  ...with a preview image
    ihmdep update 9-DXAM --image f.png    add a preview image to an entry
    ihmdep run model.cif                  deposit and block until processing ends
    ihmdep get_status                     list entries, newest first
    ihmdep get_status 9-DXAM              workflow; process, plus an exit code
    ihmdep get_status 9-DXAM --process    just one of them, for a script
    ihmdep set_status 9-DXAM --to DEPO    move it to another workflow state
    ihmdep download 9-DXAM                fetch its generated mmCIF and reports
    ihmdep delete 9-DXAM                  remove a pre-submit entry

Listings are aligned on a terminal and tab-separated when piped, with a
'#'-prefixed header, so columns containing spaces still split cleanly:
    ihmdep get_status | awk -F'\t' '!/^#/ && $5 ~ /^Error/ {print $1}'

Targets the production server (catalog 1) unless --mode dev / --host / --catalog
says otherwise; those may be given before or after the subcommand.
"""

import os
import shlex
import sys
import time

from deriva.core import urlquote

from . import _common as common
from ._common import (add_rids, add_wait, check, collect_rids,
                      confirm, connect, details_block, emit_table, get,
                      get_batched, mine,
                      prepare_asset, put_asset, require_rids, whoami)

MODES = {
    "dev": ("data-dev.pdb-ihm.org", "99"),
    "production": ("data.pdb-ihm.org", "1"),
}


def asset_path(column):
    """Where a given asset column's files go; the same annotation the web UI obeys."""
    return ("/schema/PDB/table/entry/column/%s/annotation/"
            "tag%%3Aisrd.isi.edu%%2C2017%%3Aasset" % column)


# The mmCIF annotation carries its own .cif filter; the image one carries no
# filter at all, so that restriction is ours: the types the official uploader
# (deriva-upload-cli) takes.
MMCIF_EXT = (".cif", ".CIF")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".PNG", ".JPG", ".JPEG")

# The server gives every entry deposited without an image this placeholder
# (the PDB:entry insert trigger), so "no image yet" means this file rather than
# an empty column.
PLACEHOLDER_IMAGE = ("missing_image.png", "27994cb72f6d4780d9b09a85b9b5ac50")
IMAGE_COLUMNS = ("Image_File_URL", "Image_File_Name", "Image_File_MD5", "Image_File_Bytes")

# Files worth pulling back, and which table they hang off. Generated files join
# on entry.id ("D_<RID>"); error files join on the RID itself.
GENERATED = {
    "full": "Validation: Full PDF",
    "summary": "Validation: Summary PDF",
    "mmcif": "mmCIF",          # the pipeline's own mmCIF, not the one deposited
}
# Error files sit in a separate table and are produced even for entries that
# processed cleanly, so they stay opt-in rather than joining the default set.
SELECTORS = tuple(GENERATED) + ("logs",)

# Process_Status is free text from a controlled vocabulary; classify by shape
# rather than enumerating, since the pipeline adds new phases over time.
PENDING_PREFIX = "In progress"
ERROR_PREFIX = "Error"
PENDING_EXACT = {
    "New (trigger backend process)",
    "Reprocess (trigger backend process after Error)",
    "Resume (trigger backend process)",
}
DONE = "Success"

# Deletable only while the entry is still purely the depositor's: before the
# backend has produced a record from it.
PRE_SUBMIT = ("DRAFT", "DEPO")

# A depositor drives exactly two transitions, and each is only legal from one
# state: hand a draft to the backend, then hand a processed record to curation.
# Everything else in the vocabulary is written by the backend or by curators.
TRANSITIONS = {
    "DEPO": "DRAFT",            # DRAFT  -> DEPO
    "SUBMIT": "RECORD READY",   # RECORD READY -> SUBMIT
}
USER_SETTABLE = tuple(TRANSITIONS)


# --------------------------------------------------------------------------
# upload
# --------------------------------------------------------------------------

def do_upload(args):
    path = args.ihmcif or args.file
    if not path:
        sys.exit("no mmCIF given (pass it positionally or with --ihmcif)")
    if args.file and args.ihmcif and args.file != args.ihmcif:
        sys.exit("two different mmCIF files given (%s and --ihmcif %s)" % (args.file, args.ihmcif))

    # Salt before hashing: the whole point is a different md5.
    path, image = common.apply_salt(args, path, args.image)

    catalog, store = connect()
    uid = whoami(catalog).rsplit("/", 1)[-1]

    # Validate every input before anything is looked up or written, so a bad
    # file is rejected on its own merits rather than short-circuiting on dedupe.
    mmcif_asset = get(catalog, asset_path("mmCIF_File_URL"))
    mmcif_prep = prepare_asset(path, mmcif_asset, MMCIF_EXT)
    image_asset = image_prep = None
    if image:
        image_asset = get(catalog, asset_path("Image_File_URL"))
        image_prep = prepare_asset(image, image_asset, IMAGE_EXT)

    # The server keeps one entry per file and one per file name for each
    # depositor, so look for both before anything is uploaded.
    table = get(catalog, "/schema/PDB/table/entry")
    clashes = common.taken(catalog, "PDB:entry", table["keys"],
                           {"mmCIF_File_MD5": mmcif_prep["md5"],
                            "mmCIF_File_Name": mmcif_prep["name"]})
    same = [row for cols, row in clashes if "mmCIF_File_MD5" in cols]
    if same:
        return already_deposited(same[0], image_prep, image, args.force)
    if clashes:
        e = clashes[0][1]
        sys.exit("%s: your entry %s (%s / %s) was deposited from a different file of\n"
                 "the same name, and the server keeps one entry per file name.\n"
                 "To deposit this file as a new entry, rename it."
                 % (path, e["RID"], e["Workflow_Status"], e["Process_Status"]))

    mmcif = put_asset(store, mmcif_prep, mmcif_asset, uid)
    row = {
        "mmCIF_File_URL": mmcif["URL"],
        "mmCIF_File_Name": mmcif["Name"],
        "mmCIF_File_MD5": mmcif["MD5"],
        "mmCIF_File_Bytes": mmcif["Bytes"],
        "Method_Details": args.method,
        # DEPO is what hands the entry to the backend; DRAFT parks it for the UI.
        "Workflow_Status": "DRAFT" if args.draft else "DEPO",
    }
    if image_prep:
        image = put_asset(store, image_prep, image_asset, uid)
        row.update({"Image_File_URL": image["URL"], "Image_File_Name": image["Name"],
                    "Image_File_MD5": image["MD5"], "Image_File_Bytes": image["Bytes"]})

    # Every column this row does not supply is left to the server. ERMrest takes
    # a column named in neither the row nor defaults= as supplied (as NULL) and
    # refuses the insert when the client may not write it, so the list comes
    # from the live table: a fixed one broke on dev's Accession_Issue_Time.
    defaults = [c["name"] for c in table["column_definitions"] if c["name"] not in row]

    created = check(catalog.post, "/entity/PDB:entry?defaults=" + ",".join(defaults),
                    json=[row]).json()
    # Flushed: under `run` the RID is the one line a caller wants straight
    # away, and a piped stdout would otherwise hold it until processing ends.
    print(created[0]["RID"], flush=True)
    return created[0]["RID"]


def already_deposited(e, image_prep, image, force):
    """This exact file is already an entry of yours: report it as the result."""
    print("Already deposited as %s (%s / %s)."
          % (e["RID"], e["Workflow_Status"], e["Process_Status"]), file=sys.stderr)
    if force:
        print("--force cannot deposit it again: the server keeps one entry per file.\n"
              "For a test copy that is new to the server, use --salt.", file=sys.stderr)
    if image_prep and e["Image_File_MD5"] != image_prep["md5"]:
        print("The image was not added. To %s it:\n    ihmdep update %s --image %s%s"
              % ("replace that entry's image with" if has_image(e) else "add",
                 e["RID"], shlex.quote(image), " --force" if has_image(e) else ""),
              file=sys.stderr)
    print(e["RID"], flush=True)
    return e["RID"]


def do_run(args):
    """upload, then block until the backend finishes. Exits with status's code."""
    if args.draft:
        # Refuse before uploading: a DRAFT is never processed, so there would
        # be nothing to wait for, only an entry made by a command that failed.
        sys.exit("run waits for processing, which never starts for a DRAFT.\n"
                 "Use `ihmdep upload --draft` instead.")
    rid = do_upload(args)
    args.rids, args.wait = [rid], True     # run always waits
    print("deposited %s; waiting for processing" % rid, file=sys.stderr)
    do_status(args)


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def has_image(row):
    """Whether the entry has an image of its own rather than the placeholder."""
    name, md5 = PLACEHOLDER_IMAGE
    return bool(row["Image_File_MD5"]) and row["Image_File_MD5"] != md5 \
        and row["Image_File_Name"] != name


def do_update(args):
    """Change an existing entry. For now that is its preview image."""
    if not args.image:
        sys.exit("nothing to update: give --image FILE")

    catalog, store = connect()
    # Check the file before anything is looked up, as upload does.
    image_asset = get(catalog, asset_path("Image_File_URL"))
    prepared = prepare_asset(args.image, image_asset, IMAGE_EXT)

    # tcrs() is the server's own answer to "may this client change these
    # columns of this row", so the workflow rules stay in the catalog's policy
    # (owner only, and only up to RECORD READY or after a processing error).
    rows = get(catalog, "/attributegroup/PDB:entry/RID=%s/RID,RCB,Workflow_Status,"
                        "Process_Status,Image_File_Name,Image_File_MD5;tcrs:=tcrs(RID)"
                        % urlquote(args.rid))
    if not rows:
        print("%s: unknown RID" % args.rid, file=sys.stderr)
        sys.exit(3)
    row = rows[0]
    state = "%s / %s" % (row["Workflow_Status"], row["Process_Status"])

    # Already done first, so re-running a script stays a quiet success even
    # once the entry has moved past the states that allow changes.
    if row["Image_File_MD5"] == prepared["md5"]:
        print("%s already has this image." % args.rid, file=sys.stderr)
        print(args.rid)
        return args.rid
    # Then whether a change is possible at all, before suggesting --force for
    # one that would only be refused next.
    allowed = (row.get("tcrs") or {}).get("column_update") or {}
    if not all(allowed.get(c) for c in IMAGE_COLUMNS):
        sys.exit("%s is %s, and its image can no longer be changed by this account.\n"
                 "A depositor may change an entry's image while it is DRAFT, DEPO or\n"
                 "RECORD READY, or after a processing error." % (args.rid, state))
    if has_image(row) and not args.force:
        sys.exit("%s already has an image (%s). Use --force to replace it."
                 % (args.rid, row["Image_File_Name"]))

    # The asset path is the entry creator's, as the column's url_pattern says.
    image = put_asset(store, prepared, image_asset, row["RCB"].rsplit("/", 1)[-1])
    updated = check(catalog.put, "/attributegroup/PDB:entry/RID;" + ",".join(IMAGE_COLUMNS),
                    json=[{"RID": row["RID"], "Image_File_URL": image["URL"],
                           "Image_File_Name": image["Name"], "Image_File_MD5": image["MD5"],
                           "Image_File_Bytes": image["Bytes"]}]).json()
    # A PUT whose key matches nothing answers 200 with an empty list.
    if len(updated) != 1:
        sys.exit("%s: the entry is gone; nothing was changed" % args.rid)
    # An image changes nothing else: the server reprocesses an entry only for a
    # new mmCIF or a new Workflow_Status.
    print("%s: image is now %s (%s)" % (args.rid, image["Name"], state), file=sys.stderr)
    print(args.rid)
    return args.rid


def is_pending(state):
    return state is None or state in PENDING_EXACT or state.startswith(PENDING_PREFIX)


def is_error(state):
    return bool(state) and state.startswith(ERROR_PREFIX)


def is_running(row):
    """Whether the backend still has work queued or under way for this entry.

    Process_Status alone over-reports. A DRAFT carries the column default
    "New (trigger backend process)", yet nothing runs until it is set to DEPO;
    and ERROR is final whatever Process_Status still says.
    """
    return row["Workflow_Status"] not in ("DRAFT", "ERROR") and is_pending(row["Process_Status"])


def poll_status(rids, verbose):
    """Fetch each RID's state once. Returns [(rid, row-or-None)] in request order."""
    catalog, _ = connect()   # rebuilt each poll so a long --wait refreshes its token
    cols = (LIST_COLS + ",Record_Status_Detail") if verbose else \
           "RID,Workflow_Status,Process_Status"
    found = {r["RID"]: r for r in
             get_batched(catalog, "/attribute/PDB:entry/RID=", rids, "/" + cols)}
    return [(rid, found.get(rid)) for rid in rids]


def status_code(results):
    """3 unknown > 2 pending > 1 error > 0 done.

    Pending outranks Error deliberately: a batch with one failure and one still
    running should keep polling rather than stop early. A DRAFT is pending too
    -- it is not finished -- but nothing will move it, so --wait stops on it.
    """
    rows = [row for _, row in results]
    if any(row is None for row in rows):
        return 3
    if any(is_running(r) or r["Workflow_Status"] == "DRAFT" for r in rows):
        return 2
    if any(is_error(r["Process_Status"]) or r["Workflow_Status"] == "ERROR" for r in rows):
        return 1
    return 0


# One row shape shared by the listing and by `get_status -v`, so a verbose
# lookup shows the same columns as the listing rather than a bare word.
# Either status column, or both. "Success" on its own is ambiguous -- it is
# the same word after the DEPO run and after the post-SUBMIT one -- and only
# Workflow_Status says which stage it belongs to.
STATUS_FIELDS = (("workflow", "WORKFLOW", "Workflow_Status"),
                 ("process", "PROCESS", "Process_Status"))

LIST_COLS = "RID,RMT,Accession_Code,Workflow_Status,Process_Status,mmCIF_File_Name"
LIST_HEADERS = ["RID", "MODIFIED", "ACCESSION", "WORKFLOW", "PROCESS", "FILE"]


def list_row(r):
    return [r["RID"], r["RMT"][:16], r["Accession_Code"] or "-", r["Workflow_Status"],
            r["Process_Status"] or "", r["mmCIF_File_Name"] or "-"]


def note_for(r):
    return details_block(r["Record_Status_Detail"]) if r.get("Record_Status_Detail") else None


def list_entries(args):
    """No RIDs asked for: show the table, newest first.

    Always exits 0 -- this is a listing, and an old failed entry sitting in it
    shouldn't fail the caller's shell the way `status <RID>` deliberately does.
    """
    catalog, _ = connect()
    scope = "" if args.all else "/" + mine(catalog)
    cols = LIST_COLS + (",Record_Status_Detail" if args.verbose else "")
    rows, notes = [], []
    for r in get(catalog, "/attribute/PDB:entry%s/%s@sort(RMT::desc::)" % (scope, cols)):
        rows.append(list_row(r))
        notes.append(note_for(r) if args.verbose else None)
    emit_table(LIST_HEADERS, rows, notes=notes)


def do_status(args):
    rids = collect_rids(args)
    if not rids:
        return list_entries(args)

    results = poll_status(rids, args.verbose)
    code = status_code(results)
    while args.wait and code == 2:
        waiting = sum(1 for _, row in results if row and is_running(row))
        if not waiting:
            break       # only DRAFTs left, and those wait for their depositor
        print("%d/%d still running; next check in %ds"
              % (waiting, len(rids), args.interval), file=sys.stderr)
        time.sleep(args.interval)
        results = poll_status(rids, args.verbose)
        code = status_code(results)
    if args.wait:
        for rid, row in results:
            if row and row["Workflow_Status"] == "DRAFT":
                print("%s is DRAFT: nothing runs until `ihmdep set_status %s --to DEPO`"
                      % (rid, rid), file=sys.stderr)

    asked = [f for f in STATUS_FIELDS if getattr(args, f[0], False)]

    if args.verbose:
        # Full listing row, then the detail -- same columns as a bare `get_status`.
        rows, notes = [], []
        for rid, row in results:
            if not row:
                sys.stdout.flush()
                print("%s: unknown RID" % rid, file=sys.stderr)
                continue
            rows.append(list_row(row))
            notes.append(note_for(row))
        emit_table(LIST_HEADERS, rows, notes=notes)
        sys.exit(code)

    if len(rids) == 1:
        rid, row = results[0]
        if not row:
            print("%s: unknown RID" % rid, file=sys.stderr)
            sys.exit(code)
        if asked:
            # Asked for by name: tab-separated, like every other
            # machine-readable output here, so `cut -f1` works.
            print("\t".join(row[col] or "-" for _, _, col in asked))
        else:
            # Unasked, report both. "Success" on its own cannot say whether it
            # came from the DEPO run or the post-SUBMIT one, and a reader
            # wants the stage. A script that needs one value names it:
            # `--process` is the bare word this used to print.
            both = "; ".join(row[col] for _, _, col in STATUS_FIELDS if row[col])
            print(both or "-")
        sys.exit(code)

    fields = asked or list(STATUS_FIELDS)
    rows = []
    for rid, row in results:
        if not row:
            sys.stdout.flush()
            print("%s: unknown RID" % rid, file=sys.stderr)
            continue
        rows.append([rid] + [row[col] or "-" for _, _, col in fields])
    emit_table(["RID"] + [head for _, head, _ in fields], rows)
    sys.exit(code)


# --------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------

def do_set_status(args):
    rids = require_rids(args)

    # argparse has already limited --to to USER_SETTABLE. Process_Status is not
    # settable at all (update: False for depositors) -- the backend owns it.
    catalog, _ = connect()
    results = poll_status(rids, False)
    if any(row is None for _, row in results):
        for rid, row in results:
            if row is None:
                print("%s: unknown RID" % rid, file=sys.stderr)
        sys.exit(3)

    required = TRANSITIONS[args.to]
    wrong = [(rid, row["Workflow_Status"]) for rid, row in results
             if row["Workflow_Status"] != required]
    if wrong:
        for rid, state in wrong:
            print("  %s: is %s, but --to %s is only allowed from %s"
                  % (rid, state, args.to, required), file=sys.stderr)
        sys.exit("no records changed")

    for rid, row in results:
        print("  %s  %s -> %s" % (rid, row["Workflow_Status"], args.to), file=sys.stderr)
    if not args.yes and not confirm("Apply to %d record(s)? [y/N] " % len(rids)):
        sys.exit("aborted")

    check(catalog.put, "/attributegroup/PDB:entry/RID;Workflow_Status",
          json=[{"RID": rid, "Workflow_Status": args.to} for rid in rids])

    # Read back rather than echo the request, so the printed state is the truth
    # -- including an entry deleted in the meantime.
    updated = [(r, row["Workflow_Status"] if row else "(gone)")
               for r, row in poll_status(rids, False)]
    if len(rids) == 1:
        print(updated[0][1])
    else:
        emit_table(["RID", "WORKFLOW"], [list(u) for u in updated])


def not_deletable(row):
    """Why this entry may not be deleted, or None if it may be."""
    if row["Accession_Code"]:
        return "has accession %s" % row["Accession_Code"]
    state = row["Workflow_Status"]
    if state in PRE_SUBMIT:
        return None
    return "is %s; only %s may be deleted" % (state, " and ".join(PRE_SUBMIT))


def do_delete(args):
    rids = require_rids(args)

    catalog, _ = connect()
    found = {r["RID"]: r for r in get_batched(
        catalog, "/attribute/PDB:entry/RID=", rids,
        "/RID,id,Workflow_Status,Process_Status,Accession_Code,mmCIF_File_Name")}
    if len(found) != len(rids):
        for rid in rids:
            if rid not in found:
                print("%s: unknown RID" % rid, file=sys.stderr)
        sys.exit(3)

    # 108 tables reference PDB:entry -- 92 cascade, the rest block. Past SUBMIT
    # the entry is the system's, not the depositor's, so refuse rather than
    # discover that halfway through a partial cascade.
    blocked = [(rid, why) for rid, why in
               ((rid, not_deletable(found[rid])) for rid in rids) if why]
    if blocked:
        for rid, why in blocked:
            print("  %s: refusing to delete -- %s" % (rid, why), file=sys.stderr)
        sys.exit("deletable here only while %s, and without an accession code.\n"
                 "Further along, delete it from the web interface instead: %s"
                 % (" or ".join(PRE_SUBMIT), common.URL))

    for rid in rids:
        row = found[rid]
        print("  %s  %-13s %s" % (rid, row["Workflow_Status"], row["mmCIF_File_Name"] or "-"),
              file=sys.stderr)
    print("This also cascades to any parsed mmCIF content for these entries.", file=sys.stderr)
    if not args.yes and not confirm("Delete %d entr(y/ies)? [y/N] " % len(rids)):
        sys.exit("aborted")

    for rid in rids:
        check(catalog.delete, "/entity/PDB:entry/RID=%s" % urlquote(rid))
        print(rid)


def wanted_types(args):
    """Which generated File_Types to fetch.

    Every selector narrows, and they combine. None of them means everything
    the pipeline generated -- the mmCIF included, since for most of an entry's
    life it is the only generated file there is. `--logs` selects nothing here;
    error files come from their own table.
    """
    picked = [k for k in SELECTORS if getattr(args, k)]
    if not picked:
        return list(GENERATED.values())
    return [GENERATED[k] for k in picked if k in GENERATED]


def do_download(args):
    rids = require_rids(args)
    catalog, store = connect()

    wanted = wanted_types(args)

    # Generated files join on entry.id ("D_<RID>"), error files on the RID.
    ids = {r["RID"]: r["id"] for r in
           get_batched(catalog, "/attribute/PDB:entry/RID=", rids, "/RID,id")}

    targets = {r: [] for r in rids}
    if ids and wanted:
        by_id = {v: k for k, v in ids.items()}
        for r in get_batched(catalog, "/attribute/PDB:Entry_Generated_File/Structure_Id=",
                             list(ids.values()),
                             "/Structure_Id,File_Type,File_Name,File_URL"):
            if r["File_Type"] in wanted:
                targets[by_id[r["Structure_Id"]]].append((r["File_Name"], r["File_URL"]))
    if args.logs:
        for r in get_batched(catalog, "/attribute/PDB:Entry_Error_File/Entry_RID=",
                             rids, "/Entry_RID,File_Name,File_URL"):
            targets[r["Entry_RID"]].append((r["File_Name"], r["File_URL"]))

    os.makedirs(args.outdir, exist_ok=True)
    missing, unknown = [], []
    for rid in rids:
        if rid not in ids:
            unknown.append(rid)
            sys.stdout.flush()
            print("%s: unknown RID" % rid, file=sys.stderr)
            continue
        if not targets[rid]:
            missing.append(rid)
            sys.stdout.flush()
            print("%s: nothing to download yet (%s)" % (rid, ids[rid]), file=sys.stderr)
            continue
        for name, url in targets[rid]:
            # Generated names already carry the entry id; keep them, but never
            # let one name a directory -- they come from the server.
            dest = os.path.join(args.outdir, os.path.basename(name))
            # File_URL is version-qualified; always use it verbatim.
            check(store.get_obj, url, destfilename=dest)
            print(dest)

    if unknown:
        sys.exit(3)         # as get_status: the RID itself is wrong
    if missing:
        sys.exit(1 if len(missing) == len(rids) else
                 "%d of %d RIDs had nothing to download" % (len(missing), len(rids)))


# --------------------------------------------------------------------------

def configure(args):
    return common.configure(args, MODES, "IHMDEP")


def main():
    p, add = common.build_parser(__doc__, MODES)

    def deposit_args(q):
        q.add_argument("file", nargs="?", help="mmCIF file to deposit")
        q.add_argument("--ihmcif", metavar="FILE",
                       help="same as the positional argument; accepted for clarity")
        q.add_argument("--image", metavar="FILE", help="optional preview image (.png or .jpg)")
        q.add_argument("--method", default="Integrative modeling", help="Method_Details value")
        q.add_argument("--draft", action="store_true",
                       help="create as DRAFT instead of DEPO, so nothing runs yet")
        # Kept so scripts that pass it still run. It cannot do what it used to
        # promise: the server allows one entry per file per depositor.
        q.add_argument("-f", "--force", action="store_true",
                       help="has no effect: the server keeps one entry per file "
                            "(--salt deposits a fresh test copy)")
        common.add_salt(q)
        return q

    common.add_auth_commands(add)

    q = deposit_args(add("upload", help="deposit an entry"))
    q.set_defaults(func=do_upload)

    q = deposit_args(add("run", help="deposit, then wait for processing to finish"))
    q.add_argument("-v", "--verbose", action="store_true", help="print the full status detail")
    common.add_interval(q)
    q.set_defaults(func=do_run)

    q = add("update", help="change an existing entry: add or replace its preview image")
    q.add_argument("rid", help="the entry's RID")
    q.add_argument("--image", metavar="FILE", help="preview image (.png or .jpg)")
    q.add_argument("-f", "--force", action="store_true",
                   help="replace an image the entry already has")
    q.set_defaults(func=do_update)

    q = add("get_status", help="with RIDs: processing state, exit 0=done 1=error 2=pending "
                               "3=unknown. Without: list entries, newest first")
    add_rids(q, "RIDs to check; '-' reads them from stdin; none lists entries")
    q.add_argument("-a", "--all", action="store_true",
                   help="when listing, everyone's entries rather than just yours")
    q.add_argument("-v", "--verbose", action="store_true", help="print the full status detail")
    # Which status to report. Neither given reports both: "WORKFLOW; PROCESS"
    # for a single RID, both columns for several.
    q.add_argument("--workflow", action="store_true",
                   help="report Workflow_Status (DRAFT, DEPO, RECORD READY, SUBMIT, ...)")
    q.add_argument("--process", action="store_true",
                   help="report Process_Status (the backend's progress)")
    add_wait(q)
    q.set_defaults(func=do_status)

    q = add("set_status", help="move entries to another Workflow_Status "
                               "(Process_Status is backend-owned and not settable)")
    add_rids(q)
    q.add_argument("--to", required=True, choices=list(USER_SETTABLE),
                   help="DEPO (only from DRAFT) or SUBMIT (only from RECORD READY); "
                        "every other value is written by the backend or by curators")
    q.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    q.set_defaults(func=do_set_status)

    q = add("delete", help="delete entries, DRAFT or DEPO only")
    add_rids(q)
    q.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    q.set_defaults(func=do_delete)

    q = add("download", help="fetch entries' generated files")
    add_rids(q)
    q.add_argument("-o", "--outdir", default=".")
    # Default: the generated mmCIF and both reports. Each flag narrows to that
    # one kind, and they may be combined.
    q.add_argument("--mmcif", action="store_true", help="the generated mmCIF only")
    q.add_argument("--full", action="store_true", help="full validation report only")
    q.add_argument("--summary", action="store_true", help="summary validation report only")
    q.add_argument("--logs", action="store_true", help="error/diagnostic files only")
    q.set_defaults(func=do_download)

    args = p.parse_args()
    configure(args)
    common.dispatch(args)


if __name__ == "__main__":
    main()
