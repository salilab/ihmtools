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
    ihmdep.py get_status 9-DXAM           workflow; process, plus an exit code
    ihmdep.py get_status 9-DXAM --process   just one of them, for a script
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

import requests
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
# re-upload -- replacing the submitted file on an entry past deposition
#
# Admin-only, and narrow on purpose. The backend re-reads the submitted mmCIF
# at SUBMIT, SUBMISSION COMPLETE and RELEASE READY, copying categories it
# holds nowhere else straight out of it, so an unusable original strands the
# entry at whichever of those stages it reached. Replacing the file fixes that
# -- but the entry trigger's own recovery clause would send a changed file to
# DEPO, which reloads the parsed content and cannot be re-run over content
# that is already there. Hence one statement, and these guards.
# --------------------------------------------------------------------------

# Where to re-arm, keyed by the failure that stranded the entry: the inverse
# of the backend's own error dispatch. All three re-enter through its export
# step, which only reads the loaded content. DEPO is deliberately absent.
REUPLOAD_STAGE = {
    "Error: generating mmCIF file": "SUBMIT",
    "Error: generating system files": "SUBMISSION COMPLETE",
    "Error: releasing entry": "RELEASE READY",
}
DEPO_ERROR = "Error: processing uploaded mmCIF file"

# Exit codes, so a shell loop can tell "skip this one" from "stop". 0/1/2/3
# keep get_status's meanings; 4 is new and means every later entry will fail
# the same way, which is the one case worth breaking the loop for.
REUPLOAD_OK = 0
REUPLOAD_REFUSED = 1
REUPLOAD_NOOP = 2
REUPLOAD_UNKNOWN = 3
REUPLOAD_ENVIRONMENT = 4

REUPLOAD_COLS = ("RID", "Workflow_Status", "Process_Status", "mmCIF_File_MD5",
                 "mmCIF_File_Name", "Accession_Code")


def reupload_entry(catalog, rid):
    """One entry's row, with Manual_Processing when this account may see it.

    Naming an admin-only column in a projection is answered with 409 "does not
    exist" rather than 403, so the retry without it is how a non-admin account
    gets a readable row at all -- and the missing key is what the verdict reads
    as "wrong account".
    """
    cols = ",".join(REUPLOAD_COLS)
    try:
        rows = get(catalog, "/attribute/PDB:entry/RID=%s/%s,Manual_Processing"
                            % (urlquote(rid), cols))
    except requests.HTTPError as e:
        if e.response is None or e.response.status_code != 409:
            raise
        rows = get(catalog, "/attribute/PDB:entry/RID=%s/%s" % (urlquote(rid), cols))
    return rows[0] if rows else None


def reupload_verdict(row, new_md5):
    """(exit code, why, target Workflow_Status) for one entry.

    Pure, so the guards can be read and tested without a catalog.

    The order matters. A finished entry has the switch back at False *and* the
    md5 already matching, so "already done" has to be settled before the switch
    is judged -- otherwise re-running the same RID list reports every success
    as a refusal and an interrupted loop cannot be resumed.
    """
    if row is None:
        return REUPLOAD_UNKNOWN, "unknown RID", None
    if row.get("mmCIF_File_MD5") == new_md5:
        return REUPLOAD_NOOP, "already carries this file", None
    if "Manual_Processing" not in row:
        return (REUPLOAD_ENVIRONMENT,
                "Manual_Processing is not visible to this account -- it is admin-only, "
                "and without it this command cannot stage the change safely", None)
    if row["Manual_Processing"] is not True:
        return (REUPLOAD_REFUSED,
                "Manual_Processing is %r, not True: the entry is not parked, so the "
                "backend may already have it" % (row["Manual_Processing"],), None)

    state = row.get("Process_Status")
    if state == DEPO_ERROR:
        return (REUPLOAD_REFUSED,
                "%r is retried at DEPO, which reloads the parsed content and cannot "
                "be re-run over an entry that already holds it" % (state,), None)
    stage = REUPLOAD_STAGE.get(state)
    if stage is None:
        return (REUPLOAD_REFUSED,
                "Process_Status is %r; only a failed export can be re-armed (%s)"
                % (state, ", ".join(sorted(REUPLOAD_STAGE))), None)
    return REUPLOAD_OK, None, stage


def reupload_row(rid, asset, stage):
    """The single atomic PUT: swap the file, unpark the entry, re-arm it.

    One statement on purpose. The trigger's recovery clause tests
    *NEW*.Workflow_Status, so leaving the status at ERROR in this same write is
    exactly what reroutes the entry to DEPO. Last_mmCIF_File_MD5 is absent
    deliberately -- the trigger moves the old md5 there itself, and writing it
    from here would overwrite the server's own bookkeeping.
    """
    return {"RID": rid,
            "mmCIF_File_URL": asset["URL"], "mmCIF_File_Name": asset["Name"],
            "mmCIF_File_MD5": asset["MD5"], "mmCIF_File_Bytes": asset["Bytes"],
            "Manual_Processing": False, "Workflow_Status": stage}


def do_reupload(args):
    """Replace one entry's submitted mmCIF and hand it back to the backend."""
    if not os.path.isfile(args.file):
        sys.exit("%s: no such file" % args.file)

    catalog, store = connect()
    row = reupload_entry(catalog, args.rid)
    asset = get(catalog, asset_path("mmCIF_File_URL"))
    # Hash before anything is written: the no-op verdict needs the new md5, and
    # a file this catalog would refuse should be refused on its own merits.
    prep = prepare_asset(args.file, asset, MMCIF_EXT)

    code, why, stage = reupload_verdict(row, prep["md5"])
    if code != REUPLOAD_OK:
        print("%s: %s" % (args.rid, why), file=sys.stderr)
        sys.exit(code)

    uid = whoami(catalog).rsplit("/", 1)[-1]
    print("  %s  %s / %s -> %s, Manual_Processing false"
          % (args.rid, row["Workflow_Status"], row["Process_Status"], stage), file=sys.stderr)
    print("  file    %s (%d bytes, md5 %s), replacing md5 %s"
          % (prep["name"], prep["size"], prep["md5"], row["mmCIF_File_MD5"]), file=sys.stderr)
    print("  hatrac  %s" % common.hatrac_target(asset, uid, prep["md5"], prep["ext"]),
          file=sys.stderr)

    if args.dry_run:
        print("dry run: nothing written", file=sys.stderr)
        return

    if not args.yes and not confirm("Replace the file on %s? [y/N] " % args.rid):
        sys.exit("aborted")

    # Hatrac first: it is outside the transaction below but idempotent, so a
    # retry after a failed PUT costs nothing.
    uploaded = put_asset(store, prep, asset, uid)
    payload = reupload_row(args.rid, uploaded, stage)
    cols = [c for c in payload if c != "RID"]
    # One statement. Splitting the file from the status lets the trigger see
    # NEW.Workflow_Status still at ERROR and reroute the entry to DEPO.
    updated = check(catalog.put, "/attributegroup/PDB:entry/RID;" + ",".join(cols),
                    json=[payload]).json()
    # An attributegroup PUT whose key matches nothing answers 200 with an empty
    # list, so the status code alone would report a typo'd RID as success.
    if len(updated) != 1:
        sys.exit("%s: the catalog matched no row; nothing was changed" % args.rid)
    print(args.rid)


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

    q = add("reupload", help="admin: replace one entry's submitted mmCIF and re-arm "
                             "the stage that failed on it (needs Manual_Processing)")
    q.add_argument("rid", help="the entry to repair; one per invocation, loop in the shell")
    q.add_argument("file", help="the corrected mmCIF to put in its place")
    q.add_argument("-n", "--dry-run", action="store_true",
                   help="run every guard and print the planned write, changing nothing")
    q.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    q.set_defaults(func=do_reupload)

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
