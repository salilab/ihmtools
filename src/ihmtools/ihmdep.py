#!/usr/bin/env python3
"""Minimal CLI for the PDB-IHM deposition system.

Built on deriva-py: ErmrestCatalog for records, HatracStore for files, and
GlobusNativeLogin for authentication.

    ihmdep.py login                       authenticate with Globus (once)
    ihmdep.py whoami                      which account you are logged in as
    ihmdep.py upload model.cif            deposit an entry
    ihmdep.py upload model.cif --image f.png    ...with a preview image
    ihmdep.py run model.cif               deposit and block until processing ends
    ihmdep.py get_status                  list entries, newest first
    ihmdep.py get_status 9-DXAM           one word + an exit code you can loop on
    ihmdep.py set_status 9-DXAM --to DEPO   move it to another workflow state
    ihmdep.py download 9-DXAM             fetch its generated mmCIF and reports
    ihmdep.py delete 9-DXAM               remove a pre-submit entry

Listings are aligned on a terminal and tab-separated when piped, with a
'#'-prefixed header, so columns containing spaces still split cleanly:
    ihmdep.py get_status | awk -F'\t' '!/^#/ && $5 ~ /^Error/ {print $1}'

Targets the production server (catalog 1) unless --mode dev / --host / --catalog
says otherwise; those may be given before or after the subcommand.
"""

import os
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
# filter at all, so that restriction is ours.
MMCIF_EXT = (".cif", ".CIF")
IMAGE_EXT = (".png", ".PNG")

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

    existing = get(catalog, "/entity/PDB:entry/mmCIF_File_MD5=%s&%s"
                            % (mmcif_prep["md5"], mine(catalog)))
    if existing and not args.force:
        e = existing[0]
        print("Already deposited as %s (%s / %s). Use --force to deposit again."
              % (e["RID"], e["Workflow_Status"], e["Process_Status"]), file=sys.stderr)
        print(e["RID"])
        return e["RID"]

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
    defaults = ["RID", "RCT", "RMT", "RCB", "RMB", "id", "Process_Status",
                "Record_Status_Detail", "Accession_Code", "Last_mmCIF_File_MD5",
                "Release_Date", "Deposit_Date", "Submitter_Flag", "Submitter_Flag_Date"]

    if image_prep:
        image = put_asset(store, image_prep, image_asset, uid)
        row.update({"Image_File_URL": image["URL"], "Image_File_Name": image["Name"],
                    "Image_File_MD5": image["MD5"], "Image_File_Bytes": image["Bytes"]})
    else:
        defaults += ["Image_File_URL", "Image_File_Name", "Image_File_MD5", "Image_File_Bytes"]

    created = check(catalog.post, "/entity/PDB:entry?defaults=" + ",".join(defaults),
                    json=[row]).json()
    print(created[0]["RID"])
    return created[0]["RID"]


def do_run(args):
    """upload, then block until the backend finishes. Exits with status's code."""
    rid = do_upload(args)
    if args.draft:
        sys.exit("%s created as DRAFT; nothing will run until it is set to DEPO." % rid)
    args.rids, args.wait = [rid], True     # run always waits
    print("deposited %s; waiting for processing" % rid, file=sys.stderr)
    do_status(args)


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def is_pending(state):
    return state is None or state in PENDING_EXACT or state.startswith(PENDING_PREFIX)


def is_error(state):
    return bool(state) and state.startswith(ERROR_PREFIX)


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
    running should keep polling rather than stop early.
    """
    rows = [row for _, row in results]
    if any(row is None for row in rows):
        return 3
    states = [r["Process_Status"] for r in rows]
    if any(is_pending(st) for st in states):
        return 2
    if any(is_error(st) for st in states) or any(r["Workflow_Status"] == "ERROR" for r in rows):
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
        waiting = sum(1 for _, row in results if row and is_pending(row["Process_Status"]))
        print("%d/%d still running; next check in %ds"
              % (waiting, len(rids), args.interval), file=sys.stderr)
        time.sleep(args.interval)
        results = poll_status(rids, args.verbose)
        code = status_code(results)

    def state_of(row):
        return row["Process_Status"] or row["Workflow_Status"]

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
        # Bare word, no header, so `$(ihmdep.py get_status RID)` is directly
        # usable. Unasked, that word stays Process_Status, as it always was.
        rid, row = results[0]
        if not row:
            print("%s: unknown RID" % rid, file=sys.stderr)
            sys.exit(code)
        if asked:
            print("\t".join(row[col] or "-" for _, _, col in asked))
        else:
            print(state_of(row))
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

    # Read back rather than echo the request, so the printed state is the truth.
    updated = poll_status(rids, False)
    if len(rids) == 1:
        print(updated[0][1]["Workflow_Status"])
    else:
        emit_table(["RID", "WORKFLOW"], [[r, row["Workflow_Status"]] for r, row in updated])


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
    missing = []
    for rid in rids:
        if rid not in ids:
            missing.append(rid)
            sys.stdout.flush()
            print("%s: unknown RID" % rid, file=sys.stderr)
            continue
        if not targets[rid]:
            missing.append(rid)
            sys.stdout.flush()
            print("%s: nothing to download yet (%s)" % (rid, ids[rid]), file=sys.stderr)
            continue
        for name, url in targets[rid]:
            # Generated names already carry the entry id; keep them as-is.
            dest = os.path.join(args.outdir, name)
            # File_URL is version-qualified; always use it verbatim.
            check(store.get_obj, url, destfilename=dest)
            print(dest)

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
        q.add_argument("--image", metavar="FILE", help="optional preview image (.png)")
        q.add_argument("--method", default="Integrative modeling", help="Method_Details value")
        q.add_argument("--draft", action="store_true",
                       help="create as DRAFT instead of DEPO, so nothing runs yet")
        q.add_argument("-f", "--force", action="store_true",
                       help="deposit again even if this file was already deposited")
        common.add_salt(q)
        return q

    common.add_auth_commands(add)

    q = deposit_args(add("upload", help="deposit an entry"))
    q.set_defaults(func=do_upload)

    q = deposit_args(add("run", help="deposit, then wait for processing to finish"))
    q.add_argument("-v", "--verbose", action="store_true", help="print the full status detail")
    common.add_interval(q)
    q.set_defaults(func=do_run)

    q = add("get_status", help="with RIDs: processing state, exit 0=done 1=error 2=pending "
                               "3=unknown. Without: list entries, newest first")
    add_rids(q, "RIDs to check; '-' reads them from stdin; none lists entries")
    q.add_argument("-a", "--all", action="store_true",
                   help="when listing, everyone's entries rather than just yours")
    q.add_argument("-v", "--verbose", action="store_true", help="print the full status detail")
    # Which status to report. Neither given keeps the long-standing behaviour:
    # a single RID prints Process_Status, several print both columns.
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
