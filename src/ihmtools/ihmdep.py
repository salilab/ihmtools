#!/usr/bin/env python3
"""Minimal CLI for the PDB-IHM deposition system.

Talks directly to DERIVA's two REST APIs -- ERMrest for records, Hatrac for
files -- so the only dependency is `requests`.

    ihmdep.py login                       authenticate with Globus (once)
    ihmdep.py upload model.cif            deposit an entry
    ihmdep.py upload model.cif --image f.png    ...with a preview image
    ihmdep.py run model.cif               deposit and block until processing ends
    ihmdep.py get_status                  list entries, newest first
    ihmdep.py get_status 9-DXAM           one word + an exit code you can loop on
    ihmdep.py set_status 9-DXAM --to DEPO   move it to another workflow state
    ihmdep.py download 9-DXAM             fetch generated reports
    ihmdep.py delete 9-DXAM               remove a pre-submit entry

Listings are aligned on a terminal and tab-separated when piped, with a
'#'-prefixed header, so columns containing spaces still split cleanly:
    ihmdep.py get_status | awk -F'\t' '!/^#/ && $5 ~ /^Error/ {print $1}'

Targets the dev server (catalog 99) unless --mode production / --host / --catalog
says otherwise; those may be given before or after the subcommand.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import webbrowser
from urllib.parse import quote, urlencode

import requests

MODES = {
    "dev": ("https://data-dev.pdb-ihm.org", "99"),
    "production": ("https://data.pdb-ihm.org", "1"),
}
DEFAULT_MODE = "dev"

# Set by configure() before any command runs.
HOST, CAT = None, None

# DERIVA's public native-app client; no secret, safe to embed.
CLIENT_ID = "8ef15ba9-2b4a-469c-a163-7fd910c9d111"
AUTH = "https://auth.globus.org/v2/oauth2"
# Hyphen, not underscore -- Globus rejects the underscore spelling with
# "Mismatching redirect URI". This is globus_sdk's own default for native apps.
REDIRECT = "https://auth.globus.org/v2/web/auth-code"
GROUP_SIGNUP = "https://app.globus.org/groups/99da042e-64a6-11ea-ad5f-0ef992ed7ca1/about"

# Read deriva-py's tokens if present, but only ever write our own.
DERIVA_TOKENS = os.path.expanduser("~/.deriva/globus-credential.json")
OUR_TOKENS = os.path.expanduser("~/.config/ihmv/tokens.json")


def asset_path(column):
    """Where a given asset column's files go; the same annotation the web UI obeys."""
    return ("/schema/PDB/table/entry/column/%s/annotation/"
            "tag%%3Aisrd.isi.edu%%2C2017%%3Aasset" % column)


# The mmCIF annotation carries its own .cif filter; the image one carries no
# filter at all, so that restriction is ours.
MMCIF_EXT = (".cif", ".CIF")
IMAGE_EXT = (".png", ".PNG")

MAX_PUT = 100 * 1024 * 1024  # single-request PUT ceiling; larger needs chunking

# Files worth pulling back, and which table they hang off. Generated files join
# on entry.id ("D_<RID>"); error files join on the RID itself.
REPORTS = {"full": "Validation: Full PDF", "summary": "Validation: Summary PDF"}
GENERATED_MMCIF = "mmCIF"

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

# Deletable only while the entry is still the depositor's, i.e. before SUBMIT.
PRE_SUBMIT = ("DRAFT", "DEPO", "RECORD READY")
# ERROR is not on that list because it spans the whole lifecycle: an entry can
# reach it from DEPO, from SUBMIT, or from SUBMISSION COMPLETE, and none of
# those carry an accession code to tell them apart. Process_Status is what says
# where it failed, and only a failure during DEPO is pre-submit.
PRE_SUBMIT_ERRORS = (
    "Error: processing uploaded mmCIF file",
    "Error: processing uploaded restraint files",
)

# The vocabulary has 11 values, but a depositor only drives these three:
# DRAFT (editing), DEPO (hand it to the backend), SUBMIT (hand it to curation).
# The rest are written by the backend or by curators, so the tool won't set them.
USER_SETTABLE = ("DRAFT", "DEPO", "SUBMIT")


class NeedLogin(Exception):
    pass


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

def discover_scopes():
    """Ask the server which Globus scopes it accepts."""
    r = requests.get(HOST + "/authn/discovery", timeout=30)
    r.raise_for_status()
    return list(r.json()["oauth2_scopes"].values())


def _is_deriva(entry):
    return "deriva_all" in (entry.get("scope") or "")


def _stored_token():
    """Most recently valid deriva token from our store, else deriva-py's."""
    for path in (OUR_TOKENS, DERIVA_TOKENS):
        if not os.path.exists(path):
            continue
        try:
            data = json.load(open(path))
        except (ValueError, OSError):
            continue
        entries = data.values() if isinstance(data, dict) and "access_token" not in data else [data]
        for entry in entries:
            if isinstance(entry, dict) and _is_deriva(entry):
                return entry
    return None


def _save_token(entry):
    os.makedirs(os.path.dirname(OUR_TOKENS), exist_ok=True)
    tmp = OUR_TOKENS + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(entry, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, OUR_TOKENS)


def _refresh(entry):
    r = requests.post(AUTH + "/token", timeout=30, data={
        "grant_type": "refresh_token",
        "refresh_token": entry["refresh_token"],
        "client_id": CLIENT_ID,
    })
    if not r.ok:
        raise NeedLogin("refresh failed (%s)" % r.status_code)
    new = r.json()
    entry = dict(entry)
    entry["access_token"] = new["access_token"]
    entry["expires_at_seconds"] = int(time.time()) + int(new["expires_in"])
    if new.get("refresh_token"):
        entry["refresh_token"] = new["refresh_token"]
    _save_token(entry)
    return entry


def access_token():
    entry = _stored_token()
    if not entry:
        raise NeedLogin("no stored credentials")
    if entry.get("expires_at_seconds", 0) - time.time() < 300:
        if not entry.get("refresh_token"):
            raise NeedLogin("token expired and no refresh token")
        entry = _refresh(entry)
    return entry["access_token"]


def do_login(args):
    scopes = " ".join(discover_scopes() + ["openid", "offline_access"])
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = AUTH + "/authorize?" + urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "scope": scopes,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "state": "_default",
        "prefill_named_grant": "ihmdep.py on " + os.uname().nodename,
    })

    print("Log in to Globus here, then paste the code below:\n\n  %s\n" % url)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    code = input("Authorization code: ").strip()
    if not code:
        sys.exit("no code entered")

    r = requests.post(AUTH + "/token", timeout=30, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    })
    if not r.ok:
        sys.exit("token exchange failed (%s): %s" % (r.status_code, r.text[:200]))
    payload = r.json()

    # The deriva token may arrive as the primary grant or alongside it.
    for entry in [payload] + payload.get("other_tokens", []):
        if _is_deriva(entry):
            entry = dict(entry)
            entry["expires_at_seconds"] = int(time.time()) + int(entry["expires_in"])
            _save_token(entry)
            print("Logged in as %s" % whoami())
            print("Credentials saved to %s" % OUR_TOKENS)
            return
    sys.exit("login succeeded but no deriva_all token was granted")


# --------------------------------------------------------------------------
# http
# --------------------------------------------------------------------------

def session():
    s = requests.Session()
    s.headers["Authorization"] = "Bearer " + access_token()
    return s


def check(r):
    if r.status_code == 401:
        raise NeedLogin("server rejected the token")
    if r.status_code == 403:
        sys.exit("Access denied. Authentication worked, but your identity is not "
                 "authorized for this catalog.\nRequest membership: " + GROUP_SIGNUP)
    if not r.ok:
        sys.exit("%s %s\n%s" % (r.status_code, r.reason, r.text[:400]))
    return r


def get(s, path):
    return check(s.get(CAT + path, timeout=60)).json()


def whoami(s=None):
    s = s or session()
    return check(s.get(HOST + "/authn/session", timeout=30)).json()["client"]["id"]


def mine(s):
    return "RCB=" + quote(whoami(s), safe="")


# --------------------------------------------------------------------------
# upload
# --------------------------------------------------------------------------

def hatrac_target(asset, uid, md5, ext):
    """Resolve an asset annotation's url_pattern for one file.

    Only the fields we can supply are substituted. Anything left over means the
    template grew a field this script doesn't know about, and guessing a path
    would put the file somewhere the pipeline won't look.
    """
    out = re.sub(r"\{\{#if _RCB\}\}.*?\{\{/if\}\}", uid, asset["url_pattern"], flags=re.S)
    out = out.replace("{{{%s}}}" % asset["md5"], md5)
    out = re.sub(r"\{\{\{_\w+\.filename_ext\}\}\}", ext, out)
    if "{{" in out:
        sys.exit("Cannot resolve this catalog's url_pattern -- it uses template fields "
                 "this script does not understand:\n  %s" % asset["url_pattern"])
    return out


def file_digest(path):
    d = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest(), base64.b64encode(d.digest()).decode()


def prepare_asset(path, asset, allowed_fallback):
    """Validate and hash one input file. Purely local -- must run before the
    dedupe lookup, or a rejected file with a familiar md5 slips through as a
    match against an existing entry."""
    if not os.path.isfile(path):
        sys.exit("%s: no such file" % path)
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1]
    allowed = tuple(asset.get("filename_ext_filter") or allowed_fallback)
    if ext not in allowed:
        sys.exit("%s: only %s is accepted here." % (path, " / ".join(allowed)))

    size = os.path.getsize(path)
    if size > MAX_PUT:
        sys.exit("%s is %.1f MB; this script does single-request uploads only "
                 "(limit %d MB).\nUse deriva-upload-cli for files this large."
                 % (path, size / 1e6, MAX_PUT // (1024 * 1024)))

    md5_hex, md5_b64 = file_digest(path)
    return {"path": path, "name": name, "ext": ext, "size": size,
            "md5": md5_hex, "md5_b64": md5_b64}


def put_asset(s, prepared, asset, uid):
    """Upload a prepared file to Hatrac. Returns the columns describing it."""
    target = hatrac_target(asset, uid, prepared["md5"], prepared["ext"])

    # Hatrac is content-addressed here, so an identical object can be reused.
    head = s.head(HOST + target, timeout=60)
    if head.status_code == 200 and head.headers.get("Content-MD5") == prepared["md5_b64"]:
        url = head.headers["Content-Location"]
    else:
        with open(prepared["path"], "rb") as fh:
            r = check(s.put(HOST + target + "?parents=true", data=fh, timeout=600, headers={
                "Content-MD5": prepared["md5_b64"],
                "Content-Type": "application/octet-stream",
                "Content-Disposition": "filename*=UTF-8''" + quote(prepared["name"]),
            }))
        url = r.text.strip()
    if url.startswith(HOST):
        url = url[len(HOST):]
    return {"URL": url, "Name": prepared["name"],
            "MD5": prepared["md5"], "Bytes": prepared["size"]}


def do_upload(args):
    path = args.ihmcif or args.file
    if not path:
        sys.exit("no mmCIF given (pass it positionally or with --ihmcif)")
    if args.file and args.ihmcif and args.file != args.ihmcif:
        sys.exit("two different mmCIF files given (%s and --ihmcif %s)" % (args.file, args.ihmcif))

    s = session()
    uid = whoami(s).rsplit("/", 1)[-1]

    # Validate every input before anything is looked up or written, so a bad
    # file is rejected on its own merits rather than short-circuiting on dedupe.
    mmcif_asset = get(s, asset_path("mmCIF_File_URL"))
    mmcif_prep = prepare_asset(path, mmcif_asset, MMCIF_EXT)
    image_asset = image_prep = None
    if args.image:
        image_asset = get(s, asset_path("Image_File_URL"))
        image_prep = prepare_asset(args.image, image_asset, IMAGE_EXT)

    existing = get(s, "/entity/PDB:entry/mmCIF_File_MD5=%s&%s" % (mmcif_prep["md5"], mine(s)))
    if existing and not args.force:
        e = existing[0]
        print("Already deposited as %s (%s / %s). Use --force to deposit again."
              % (e["RID"], e["Workflow_Status"], e["Process_Status"]), file=sys.stderr)
        print(e["RID"])
        return e["RID"]

    mmcif = put_asset(s, mmcif_prep, mmcif_asset, uid)
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
        image = put_asset(s, image_prep, image_asset, uid)
        row.update({"Image_File_URL": image["URL"], "Image_File_Name": image["Name"],
                    "Image_File_MD5": image["MD5"], "Image_File_Bytes": image["Bytes"]})
    else:
        defaults += ["Image_File_URL", "Image_File_Name", "Image_File_MD5", "Image_File_Bytes"]

    created = check(s.post(CAT + "/entity/PDB:entry?defaults=" + ",".join(defaults),
                           json=[row], timeout=60)).json()
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

def collect_rids(args):
    """RIDs from positionals and --rid together, with '-' expanded from stdin.
    Order-preserving and deduped.

    Reading stdin is opt-in via '-' rather than inferred from isatty(): stdin is
    not a terminal under cron, CI or any non-interactive shell, so an isatty()
    check turns a bare invocation there into a silent hang. Returns [] for "no
    RIDs asked for", which is the caller's cue to list.
    """
    given = list(args.rids or []) + list(getattr(args, "rid_flags", None) or [])
    out, read_stdin = [], False
    for rid in given:
        if rid != "-":
            out.append(rid)
            continue
        if read_stdin:      # '-' twice would block on an exhausted stdin
            continue
        read_stdin = True
        piped = sys.stdin.read().split()
        if not piped:
            sys.exit("no RIDs on stdin")
        out.extend(piped)
    seen = set()
    return [r for r in out if not (r in seen or seen.add(r))]


def details_block(details, indent="      "):
    """Record_Status_Detail, stored with literal \\n escapes rather than newlines."""
    text = (details or "").replace("\\n", "\n").strip()
    return "\n".join(indent + ln for ln in text.splitlines())


def emit_table(headers, rows, right=(), notes=None):
    """Aligned when stdout is a terminal, single-tab separated when piped.

    Padding is for eyes only -- several of these columns contain spaces, so a
    padded table cannot be split on whitespace. Piped output is therefore exact
    tabs for `awk -F'\\t'`. The header is '#'-prefixed so `!/^#/` drops it.

    notes: optional per-row text; goes to stderr after its row so that stdout
    stays nothing but data.
    """
    cells = [[("" if v is None else str(v)) for v in r] for r in rows]
    head = ["#" + headers[0]] + list(headers[1:])
    notes = notes or [None] * len(cells)

    if sys.stdout.isatty():
        widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(head)]
        def fmt(cols, is_head=False):
            # head[0] already carries the '#', and widths were measured with it,
            # so no extra adjustment: the label just sits one char right of data.
            out = [c.rjust(w) if i in right else c.ljust(w)
                   for i, (c, w) in enumerate(zip(cols, widths))]
            return "  ".join(out).rstrip()
    else:
        def fmt(cols, is_head=False):
            return "\t".join(cols)

    print(fmt(head, True))
    for row, note in zip(cells, notes):
        print(fmt(row))
        if note:
            sys.stdout.flush()
            print(note, file=sys.stderr)


def is_pending(state):
    return state is None or state in PENDING_EXACT or state.startswith(PENDING_PREFIX)


def is_error(state):
    return bool(state) and state.startswith(ERROR_PREFIX)


def poll_status(rids, verbose):
    """Fetch each RID's state once. Returns [(rid, row-or-None)] in request order."""
    s = session()   # rebuilt each poll so a long --wait refreshes its token
    match = "any(%s)" % ",".join(quote(r, safe="") for r in rids)
    cols = (LIST_COLS + ",Record_Status_Detail") if verbose else \
           "RID,Workflow_Status,Process_Status"
    found = {r["RID"]: r for r in get(s, "/attribute/PDB:entry/RID=%s/%s" % (match, cols))}
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
    s = session()
    scope = "" if args.all else "/" + mine(s)
    cols = LIST_COLS + (",Record_Status_Detail" if args.verbose else "")
    rows, notes = [], []
    for r in get(s, "/attribute/PDB:entry%s/%s@sort(RMT::desc::)" % (scope, cols)):
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
        # Bare word, no header, so `$(ihmdep.py get_status RID)` is directly usable.
        rid, row = results[0]
        if row:
            print(state_of(row))
        else:
            print("%s: unknown RID" % rid, file=sys.stderr)
        sys.exit(code)

    rows = []
    for rid, row in results:
        if not row:
            sys.stdout.flush()
            print("%s: unknown RID" % rid, file=sys.stderr)
            continue
        rows.append([rid, row["Workflow_Status"], state_of(row)])
    emit_table(["RID", "WORKFLOW", "PROCESS"], rows)
    sys.exit(code)


# --------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------

def confirm(prompt):
    """Ask on the terminal, not stdin -- stdin may hold the RID list."""
    try:
        tty = open("/dev/tty")
    except OSError:
        sys.exit("no terminal to confirm on; re-run with --yes")
    sys.stderr.write(prompt)
    sys.stderr.flush()
    answer = tty.readline().strip().lower()
    tty.close()
    return answer in ("y", "yes")


def do_set_status(args):
    rids = collect_rids(args)
    if not rids:
        sys.exit("no RIDs given (pass RIDs, or '-' to read them from stdin)")

    # argparse has already limited --to to USER_SETTABLE. Process_Status is not
    # settable at all (update: False for depositors) -- the backend owns it.
    s = session()
    results = poll_status(rids, False)
    if any(row is None for _, row in results):
        for rid, row in results:
            if row is None:
                print("%s: unknown RID" % rid, file=sys.stderr)
        sys.exit(3)

    for rid, row in results:
        print("  %s  %s -> %s" % (rid, row["Workflow_Status"], args.to), file=sys.stderr)
    if not args.yes and not confirm("Apply to %d record(s)? [y/N] " % len(rids)):
        sys.exit("aborted")

    check(s.put(CAT + "/attributegroup/PDB:entry/RID;Workflow_Status",
                json=[{"RID": rid, "Workflow_Status": args.to} for rid in rids], timeout=60))

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
    if state == "ERROR":
        process = row["Process_Status"] or "(none)"
        if process in PRE_SUBMIT_ERRORS:
            return None
        return "is ERROR from %r, which happens after SUBMIT" % process
    return "is %s, past pre-submit" % state


def do_delete(args):
    rids = collect_rids(args)
    if not rids:
        sys.exit("no RIDs given (pass RIDs, or '-' to read them from stdin)")

    s = session()
    match = "any(%s)" % ",".join(quote(r, safe="") for r in rids)
    found = {r["RID"]: r for r in get(
        s, "/attribute/PDB:entry/RID=%s"
           "/RID,id,Workflow_Status,Process_Status,Accession_Code,mmCIF_File_Name" % match)}
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
        sys.exit("deletable only while %s (or ERROR from a failed upload), and without an "
                 "accession code.\nTo retire a submitted entry use: %s set_status <RID> "
                 "--to ABANDONED" % (" / ".join(PRE_SUBMIT), sys.argv[0]))

    for rid in rids:
        row = found[rid]
        print("  %s  %-13s %s" % (rid, row["Workflow_Status"], row["mmCIF_File_Name"] or "-"),
              file=sys.stderr)
    print("This also cascades to any parsed mmCIF content for these entries.", file=sys.stderr)
    if not args.yes and not confirm("Delete %d entr(y/ies)? [y/N] " % len(rids)):
        sys.exit("aborted")

    for rid in rids:
        check(s.delete(CAT + "/entity/PDB:entry/RID=%s" % quote(rid, safe=""), timeout=120))
        print(rid)


def do_download(args):
    rids = collect_rids(args)
    if not rids:
        sys.exit("no RIDs given (pass RIDs, or '-' to read them from stdin)")
    s = session()

    wanted = [REPORTS[k] for k in ("full", "summary") if getattr(args, k)] or list(REPORTS.values())
    if args.mmcif:
        wanted.append(GENERATED_MMCIF)

    match = "any(%s)" % ",".join(quote(r, safe="") for r in rids)
    # Generated files join on entry.id ("D_<RID>"), error files on the RID.
    ids = {r["RID"]: r["id"] for r in get(s, "/attribute/PDB:entry/RID=%s/RID,id" % match)}

    targets = {r: [] for r in rids}
    if ids:
        id_match = "any(%s)" % ",".join(quote(v, safe="") for v in ids.values())
        by_id = {v: k for k, v in ids.items()}
        for r in get(s, "/attribute/PDB:Entry_Generated_File/Structure_Id=%s"
                        "/Structure_Id,File_Type,File_Name,File_URL" % id_match):
            if r["File_Type"] in wanted:
                targets[by_id[r["Structure_Id"]]].append((r["File_Name"], r["File_URL"]))
    if args.logs:
        for r in get(s, "/attribute/PDB:Entry_Error_File/Entry_RID=%s"
                        "/Entry_RID,File_Name,File_URL" % match):
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
            with check(s.get(HOST + url, stream=True, timeout=600)) as resp, open(dest, "wb") as fh:
                for chunk in resp.iter_content(1 << 20):
                    fh.write(chunk)
            print(dest)

    if missing:
        sys.exit(1 if len(missing) == len(rids) else
                 "%d of %d RIDs had nothing to download" % (len(missing), len(rids)))


# --------------------------------------------------------------------------

def configure(args):
    """Resolve the target server: flags > --mode > environment > dev default."""
    global HOST, CAT
    host, catalog = MODES[DEFAULT_MODE]
    host = os.environ.get("IHMDEP_HOST", host)
    catalog = os.environ.get("IHMDEP_CATALOG", catalog)
    if getattr(args, "mode", None):
        host, catalog = MODES[args.mode]
    host = getattr(args, "host", None) or host
    catalog = getattr(args, "catalog", None) or catalog
    if not host.startswith("http"):
        host = "https://" + host
    HOST = host.rstrip("/")
    CAT = "%s/ermrest/catalog/%s" % (HOST, catalog)


def main():
    # SUPPRESS so an unset option leaves no attribute behind; that lets these be
    # accepted both before and after the subcommand without the subparser's
    # defaults clobbering what the main parser already parsed.
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    g = common.add_argument_group("server")
    g.add_argument("--mode", choices=["dev", "production"],
                   help="dev = %s catalog %s (default); production = %s catalog %s"
                        % (MODES["dev"] + MODES["production"]))
    g.add_argument("--host", metavar="HOST", help="override the server hostname")
    g.add_argument("--catalog", metavar="ID", help="override the catalog number")

    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common])
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, **kw):
        return sub.add_parser(name, parents=[common], **kw)

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
        return q

    q = add("login", help="authenticate with Globus")
    q.add_argument("--no-browser", action="store_true", help="just print the URL")
    q.set_defaults(func=do_login)

    q = deposit_args(add("upload", help="deposit an entry"))
    q.set_defaults(func=do_upload)

    q = deposit_args(add("run", help="deposit, then wait for processing to finish"))
    q.add_argument("-v", "--verbose", action="store_true", help="print the full status detail")
    q.add_argument("--interval", type=int, default=30, metavar="SECS",
                   help="seconds between polls (default 30)")
    q.set_defaults(func=do_run)

    q = add("get_status", help="with RIDs: processing state, exit 0=done 1=error 2=pending "
                               "3=unknown. Without: list entries, newest first")
    q.add_argument("rids", nargs="*", metavar="RID",
                   help="RIDs to check; '-' reads them from stdin; none lists entries")
    q.add_argument("--rid", action="append", dest="rid_flags", metavar="RID",
                   help="same as a positional RID; may be repeated")
    q.add_argument("-a", "--all", action="store_true",
                   help="when listing, everyone's entries rather than just yours")
    q.add_argument("-v", "--verbose", action="store_true", help="print the full status detail")
    # A flag, not an optional value: `--wait 300` beside a variadic RID list
    # would otherwise read 300 as the interval and silently check nothing.
    q.add_argument("-w", "--wait", action="store_true",
                   help="poll until nothing is pending")
    q.add_argument("--interval", type=int, default=30, metavar="SECS",
                   help="seconds between polls (default 30)")
    q.set_defaults(func=do_status)

    q = add("set_status", help="move entries to another Workflow_Status "
                               "(Process_Status is backend-owned and not settable)")
    q.add_argument("rids", nargs="*", metavar="RID",
                   help="one or more RIDs, or '-' to read them from stdin")
    q.add_argument("--rid", action="append", dest="rid_flags", metavar="RID",
                   help="same as a positional RID; may be repeated")
    q.add_argument("--to", required=True, choices=list(USER_SETTABLE),
                   help="target Workflow_Status; the other values in the vocabulary are "
                        "written by the backend or by curators")
    q.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    q.set_defaults(func=do_set_status)

    q = add("delete", help="delete pre-submit entries (DRAFT/DEPO/RECORD READY, or "
                           "ERROR from a failed upload)")
    q.add_argument("rids", nargs="*", metavar="RID",
                   help="one or more RIDs, or '-' to read them from stdin")
    q.add_argument("--rid", action="append", dest="rid_flags", metavar="RID",
                   help="same as a positional RID; may be repeated")
    q.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    q.set_defaults(func=do_delete)

    q = add("download", help="fetch entries' generated files")
    q.add_argument("rids", nargs="*", metavar="RID",
                   help="one or more RIDs, or '-' to read them from stdin")
    q.add_argument("--rid", action="append", dest="rid_flags", metavar="RID",
                   help="same as a positional RID; may be repeated")
    q.add_argument("-o", "--outdir", default=".")
    q.add_argument("--full", action="store_true", help="full validation report only")
    q.add_argument("--summary", action="store_true", help="summary validation report only")
    q.add_argument("--mmcif", action="store_true", help="also the generated mmCIF")
    q.add_argument("--logs", action="store_true", help="also any error/diagnostic files")
    q.set_defaults(func=do_download)

    args = p.parse_args()
    configure(args)
    try:
        args.func(args)
    except NeedLogin as e:
        sys.exit("Not authenticated (%s).\nRun: %s login" % (e, sys.argv[0]))
    except KeyboardInterrupt:
        sys.exit(130)
    except BrokenPipeError:
        # Something downstream stopped reading -- `| head` is the usual case.
        # Point stdout at /dev/null so the interpreter's final flush cannot
        # raise again, then exit the way a program killed by SIGPIPE would.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass        # stdout is not a real file descriptor; nothing to do
        sys.exit(128 + 13)


if __name__ == "__main__":
    main()
