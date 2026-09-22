#!/usr/bin/env python3
"""Minimal CLI for the PDB-IHM integrative-model validation catalog (IHMV).

Built on deriva-py: ErmrestCatalog for records, HatracStore for files, and
GlobusNativeLogin for authentication.

    ihmv.py login                    authenticate with Globus (once)
    ihmv.py upload model.cif         submit a structure for validation
    ihmv.py run model.cif            upload and block until validation finishes
    ihmv.py get_status               list entries, newest first
    ihmv.py get_status 2ZJ           one word + an exit code you can loop on
    ihmv.py set_status 2QJ --to Reprocess    ask the pipeline to run it again
    ihmv.py download 2Y0 2XT         fetch those entries' validation reports
    ihmv.py delete 2Y0               remove a record and its reports

Listings are aligned on a terminal and tab-separated when piped, with a
'#'-prefixed header, so columns containing spaces still split cleanly:
    ihmv.py get_status | awk -F'\t' '!/^#/ && $3=="Success" {print $1, $6}'

Targets the dev server (catalog 199) unless --mode production / --host / --catalog
says otherwise; those may be given before or after the subcommand.
"""

import os
import sys
import time
import uuid

from deriva.core import urlquote

from . import _common as common
from ._common import (add_rids, add_wait, any_of, check, collect_rids,
                      confirm, connect, details_block, emit_table, get, mine,
                      prepare_asset, put_asset, require_rids, whoami)

MODES = {
    "dev": ("data-dev.pdb-ihm.org", "199"),
    "production": ("data.pdb-ihm.org", "101"),
}
DEFAULT_MODE = "dev"

# Where submitted files go and which extensions are allowed both come from this
# annotation, which is what the web UI follows. The bulk-upload annotation that
# deriva-upload-cli reads points somewhere else on dev; don't use it.
ASSET_ANNOTATION = ("/schema/IHMV/table/Structure_mmCIF/column/File_URL"
                    "/annotation/tag%3Aisrd.isi.edu%2C2017%3Aasset")
ALLOWED_EXT = (".cif", ".CIF")   # fallback if the annotation omits the filter
REFUSAL = ("%s: this catalog only accepts %s. Other formats upload but then "
           "fail in the validation pipeline.")
REPORTS = {"full": "Validation: Full PDF", "summary": "Validation: Summary PDF"}
PENDING = {"New", "In Progress", "Reprocess"}   # not yet terminal
# Processing_Status has no FK constraint here, so an unrecognised string would be
# written silently -- these are the only two the tool will set. The rest are the
# pipeline's own report of what happened.
SETTABLE = ("New", "Reprocess")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def do_upload(args):
    catalog, store = connect()
    uid = whoami(catalog).rsplit("/", 1)[-1]

    # Take the upload rules from the catalog rather than hardcoding them: the
    # submitted-file namespace differs between dev and production, and this is
    # the same annotation the web UI obeys.
    asset = get(catalog, ASSET_ANNOTATION)
    prep = prepare_asset(args.file, asset, ALLOWED_EXT, refusal=REFUSAL)
    title = args.title or "%s_%s" % (os.path.splitext(prep["name"])[0],
                                     uuid.uuid4().hex[:8])

    existing = get(catalog, "/entity/IHMV:Structure_mmCIF/File_MD5=%s&%s"
                            % (prep["md5"], mine(catalog)))
    if existing and not args.force:
        e = existing[0]
        print("Already submitted as %s (%s, %s). Use --force to submit again."
              % (e["RID"], e["Title"], e["Processing_Status"]), file=sys.stderr)
        print(e["RID"])
        return e["RID"]

    put = put_asset(store, prep, asset, uid)
    row = {
        "Title": title,
        "File_URL": put["URL"],
        "File_Name": put["Name"],
        "File_Bytes": put["Bytes"],
        "File_MD5": put["MD5"],
        "Description": args.description,
        "Processing_Status": "New",   # what tells the pipeline to pick it up
    }
    created = check(catalog.post,
                    "/entity/IHMV:Structure_mmCIF?defaults=RID,RCT,RMT,RCB,RMB",
                    json=[row]).json()
    print(created[0]["RID"])
    return created[0]["RID"]


def poll_status(rids, verbose):
    """Fetch each RID's state once. Returns [(rid, row-or-None)] in request order."""
    catalog, _ = connect()   # rebuilt each poll so a long --wait refreshes its token
    match = any_of(rids)
    cols = (LIST_COLS + ",Processing_Details") if verbose else "RID,Processing_Status"
    found = {r["RID"]: r for r in
             get(catalog, "/attribute/IHMV:Structure_mmCIF/RID=%s/%s" % (match, cols))}
    return [(rid, found.get(rid)) for rid in rids]


def status_code(results):
    """3 unknown > 2 pending > 1 error > 0 done.

    Pending outranks Error deliberately: a batch with one failure and one still
    running should keep polling rather than stop early.
    """
    states = [row["Processing_Status"] if row else None for _, row in results]
    if any(st is None for st in states):
        return 3
    if any(st in PENDING for st in states):
        return 2
    return 1 if any(st == "Error" for st in states) else 0


# One row shape shared by the listing and by `get_status -v`, so a verbose
# lookup shows the same columns as the listing rather than a bare word.
LIST_COLS = "RID,RMT,Processing_Status,File_Bytes,Title,File_Name"
LIST_HEADERS = ["RID", "MODIFIED", "STATUS", "BYTES", "TITLE", "FILE"]
LIST_RIGHT = {3}


def list_row(r):
    return [r["RID"], r["RMT"][:16], r["Processing_Status"], r["File_Bytes"],
            r["Title"], r["File_Name"]]


def note_for(r):
    return details_block(r["Processing_Details"]) if r.get("Processing_Details") else None


def list_entries(args):
    """No RIDs asked for: show the table, newest first.

    Always exits 0 -- this is a listing, and an old Error entry sitting in it
    shouldn't fail the caller's shell the way `get_status <RID>` deliberately does.
    """
    catalog, _ = connect()
    scope = "" if args.all else "/" + mine(catalog)
    cols = LIST_COLS + (",Processing_Details" if args.verbose else "")
    rows, notes = [], []
    for r in get(catalog, "/attribute/IHMV:Structure_mmCIF%s/%s@sort(RMT::desc::)" % (scope, cols)):
        rows.append(list_row(r))
        notes.append(note_for(r) if args.verbose else None)
    emit_table(LIST_HEADERS, rows, right=LIST_RIGHT, notes=notes)


def do_status(args):
    rids = collect_rids(args)
    if not rids:
        return list_entries(args)

    results = poll_status(rids, args.verbose)
    code = status_code(results)
    while args.wait and code == 2:
        waiting = sum(1 for _, row in results if row and row["Processing_Status"] in PENDING)
        print("%d/%d still running; next check in %ds"
              % (waiting, len(rids), args.interval), file=sys.stderr)
        time.sleep(args.interval)
        results = poll_status(rids, args.verbose)
        code = status_code(results)

    if args.verbose:
        # Full listing row, then the log -- same columns as a bare `get_status`.
        rows, notes = [], []
        for rid, row in results:
            if not row:
                sys.stdout.flush()
                print("%s: unknown RID" % rid, file=sys.stderr)
                continue
            rows.append(list_row(row))
            notes.append(note_for(row))
        emit_table(LIST_HEADERS, rows, right=LIST_RIGHT, notes=notes)
        sys.exit(code)

    if len(rids) == 1:
        # Bare word, no header, so `$(ihmv.py get_status RID)` is directly usable.
        rid, row = results[0]
        if row:
            print(row["Processing_Status"])
        else:
            print("%s: unknown RID" % rid, file=sys.stderr)
        sys.exit(code)

    rows = []
    for rid, row in results:
        if not row:
            sys.stdout.flush()
            print("%s: unknown RID" % rid, file=sys.stderr)
            continue
        rows.append([rid, row["Processing_Status"]])
    emit_table(["RID", "STATUS"], rows)
    sys.exit(code)


def do_run(args):
    """upload, then block until the pipeline finishes. Exits with status's code."""
    rid = do_upload(args)
    args.rids, args.wait = [rid], True     # run always waits
    print("submitted %s; waiting for validation" % rid, file=sys.stderr)
    do_status(args)   # exits 0 done / 1 error / 2 pending / 3 unknown


def do_set_status(args):
    rids = require_rids(args)
    if args.to not in SETTABLE:
        sys.exit("cannot set Processing_Status to %r.\nThis tool only sets %s -- the other "
                 "states are written by the pipeline to report what it did, and setting them "
                 "by hand would misreport it." % (args.to, " and ".join(SETTABLE)))

    results = poll_status(rids, False)
    if any(row is None for _, row in results):
        for rid, row in results:
            if row is None:
                print("%s: unknown RID" % rid, file=sys.stderr)
        sys.exit(3)

    for rid, row in results:
        print("  %s  %s -> %s" % (rid, row["Processing_Status"], args.to), file=sys.stderr)
    if not args.yes and not confirm("Apply to %d record(s)? [y/N] " % len(rids)):
        sys.exit("aborted")

    catalog, _ = connect()
    check(catalog.put, "/attributegroup/IHMV:Structure_mmCIF/RID;Processing_Status",
          json=[{"RID": rid, "Processing_Status": args.to} for rid in rids])

    # Read back rather than echo the request, so the printed state is the truth.
    updated = poll_status(rids, False)
    if len(rids) == 1:
        print(updated[0][1]["Processing_Status"])
    else:
        emit_table(["RID", "STATUS"], [[r, row["Processing_Status"]] for r, row in updated])


def do_delete(args):
    rids = require_rids(args)

    catalog, store = connect()
    match = any_of(rids)
    found = {r["RID"]: r for r in get(
        catalog, "/attribute/IHMV:Structure_mmCIF/RID=%s"
                 "/RID,Title,Processing_Status,File_MD5,File_URL" % match)}
    if len(found) != len(rids):
        for rid in rids:
            if rid not in found:
                print("%s: unknown RID" % rid, file=sys.stderr)
        sys.exit(3)

    # Generated_File points at the structure by text with no FK, so nothing
    # cascades -- deleting the structure alone would strand its reports.
    reports = {}
    for r in get(catalog, "/attribute/IHMV:Generated_File/Structure_mmCIF=%s"
                          "/Structure_mmCIF" % match):
        reports[r["Structure_mmCIF"]] = reports.get(r["Structure_mmCIF"], 0) + 1

    for rid in rids:
        row = found[rid]
        print("  %s  %-9s %s  (%d report(s))"
              % (rid, row["Processing_Status"], row["Title"], reports.get(rid, 0)),
              file=sys.stderr)
    if not args.yes and not confirm(
            "Delete %d record(s) and their reports? [y/N] " % len(rids)):
        sys.exit("aborted")

    for rid in rids:
        # Reports first: if the second call fails the record still exists and
        # the delete can be retried, rather than leaving orphaned reports.
        check(catalog.delete,
              "/entity/IHMV:Generated_File/Structure_mmCIF=%s" % urlquote(rid))
        check(catalog.delete,
              "/entity/IHMV:Structure_mmCIF/RID=%s" % urlquote(rid))
        print(rid)

    if args.purge_file:
        for rid in rids:
            md5, url = found[rid]["File_MD5"], found[rid]["File_URL"]
            # Hatrac objects here are content-addressed, so one file can back
            # several records; only purge once nothing else references it.
            others = get(catalog, "/attribute/IHMV:Structure_mmCIF/File_MD5=%s/RID"
                                  % urlquote(md5))
            if others:
                print("  kept %s -- still used by %s"
                      % (url.rsplit("/", 1)[-1], ", ".join(r["RID"] for r in others)),
                      file=sys.stderr)
                continue
            check(store.del_obj, url.rsplit(":", 1)[0])
            print("  purged %s" % url.rsplit("/", 1)[-1], file=sys.stderr)


def do_download(args):
    rids = require_rids(args)
    catalog, store = connect()
    wanted = [REPORTS[k] for k in ("full", "summary") if getattr(args, k)] or list(REPORTS.values())
    # ERMrest takes a disjunction, so the whole batch is one round trip.
    match = any_of(rids)

    targets = {r: [] for r in rids}
    if args.mmcif:
        for r in get(catalog, "/attribute/IHMV:Structure_mmCIF/RID=%s/RID,File_Name,File_URL" % match):
            # Report names already carry the RID; submitted files don't, so
            # prefix them when a batch could otherwise collide.
            name = r["File_Name"] if len(rids) == 1 else "%s_%s" % (r["RID"], r["File_Name"])
            targets[r["RID"]].append((name, r["File_URL"]))
    for r in get(catalog, "/attribute/IHMV:Generated_File/Structure_mmCIF=%s"
                          "/Structure_mmCIF,File_Type,File_Name,File_URL" % match):
        if r["File_Type"] in wanted:
            targets[r["Structure_mmCIF"]].append((r["File_Name"], r["File_URL"]))

    os.makedirs(args.outdir, exist_ok=True)
    missing = []
    for rid in rids:
        if not targets[rid]:
            missing.append(rid)
            sys.stdout.flush()
            print("%s: nothing to download (no reports yet, or wrong RID)" % rid, file=sys.stderr)
            continue
        for name, url in targets[rid]:
            dest = os.path.join(args.outdir, name)
            # File_URL is version-qualified and its prefix differs between
            # submitted and generated files; always use it verbatim.
            check(store.get_obj, url, destfilename=dest)
            print(dest)

    if missing:
        sys.exit(1 if len(missing) == len(rids) else
                 "%d of %d RIDs had nothing to download" % (len(missing), len(rids)))


# --------------------------------------------------------------------------

def configure(args):
    return common.configure(args, MODES, DEFAULT_MODE, "IHMV")


def main():
    p, add = common.build_parser(__doc__, MODES, DEFAULT_MODE)

    def submit_args(q):
        q.add_argument("file", help="mmCIF file")
        q.add_argument("-t", "--title", help="default: <basename>_<uuid8>")
        q.add_argument("-d", "--description")
        q.add_argument("-f", "--force", action="store_true",
                       help="submit again even if this file was already submitted")
        return q

    common.add_auth_commands(add)

    q = submit_args(add("upload", help="submit a structure for validation"))
    q.set_defaults(func=do_upload)

    q = submit_args(add("run", help="upload, then wait for validation to finish"))
    q.add_argument("-v", "--verbose", action="store_true", help="print the full processing log")
    common.add_interval(q)
    q.set_defaults(func=do_run)

    q = add("get_status", help="with RIDs: processing state, exit 0=done 1=error 2=pending "
                               "3=unknown. Without: list entries, newest first")
    add_rids(q, "RIDs to check; '-' reads them from stdin; none lists entries")
    q.add_argument("-a", "--all", action="store_true",
                   help="when listing, everyone's entries rather than just yours")
    q.add_argument("-v", "--verbose", action="store_true",
                   help="print the full processing log")
    add_wait(q)
    q.set_defaults(func=do_status)

    q = add("set_status", help="ask the pipeline to (re)run these entries")
    add_rids(q)
    q.add_argument("--to", required=True, choices=list(SETTABLE),
                   help="the state to set")
    q.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    q.set_defaults(func=do_set_status)

    q = add("delete", help="delete entries and their generated reports")
    add_rids(q)
    q.add_argument("--purge-file", action="store_true",
                   help="also delete the submitted file from Hatrac, "
                        "unless another record shares it")
    q.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    q.set_defaults(func=do_delete)

    q = add("download", help="fetch entries' validation reports")
    add_rids(q)
    q.add_argument("-o", "--outdir", default=".")
    q.add_argument("--full", action="store_true", help="full report only")
    q.add_argument("--summary", action="store_true", help="summary report only")
    q.add_argument("--mmcif", action="store_true", help="also fetch the submitted mmCIF")
    q.set_defaults(func=do_download)

    args = p.parse_args()
    configure(args)
    common.dispatch(args)


if __name__ == "__main__":
    main()
