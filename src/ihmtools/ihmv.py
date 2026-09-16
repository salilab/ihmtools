#!/usr/bin/env python3
"""Minimal CLI for the PDB-IHM integrative-model validation catalog (IHMV).

Talks directly to DERIVA's two REST APIs -- ERMrest for records, Hatrac for
files -- so the only dependency is `requests`.

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

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import uuid
import webbrowser
from urllib.parse import quote, urlencode

import requests

MODES = {
    "dev": ("https://data-dev.pdb-ihm.org", "199"),
    "production": ("https://data.pdb-ihm.org", "101"),
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

# Where submitted files go and which extensions are allowed both come from this
# annotation, which is what the web UI follows. The bulk-upload annotation that
# deriva-upload-cli reads points somewhere else on dev; don't use it.
ASSET_ANNOTATION = ("/schema/IHMV/table/Structure_mmCIF/column/File_URL"
                    "/annotation/tag%3Aisrd.isi.edu%2C2017%3Aasset")
ALLOWED_EXT = (".cif", ".CIF")   # fallback if the annotation omits the filter
MAX_PUT = 100 * 1024 * 1024  # single-request PUT ceiling; larger needs chunking
REPORTS = {"full": "Validation: Full PDF", "summary": "Validation: Summary PDF"}
PENDING = {"New", "In Progress", "Reprocess"}   # not yet terminal
# Processing_Status has no FK constraint here, so an unrecognised string would be
# written silently -- these are the only two the tool will set. The rest are the
# pipeline's own report of what happened.
SETTABLE = ("New", "Reprocess")


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
        "prefill_named_grant": "ihmv.py on " + os.uname().nodename,
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
# commands
# --------------------------------------------------------------------------

def hatrac_target(pattern, uid, md5, ext):
    """Resolve the asset annotation's url_pattern for one file.

    Only the three fields we can supply are substituted. Anything left over
    means the template grew a field this script doesn't know about, and
    guessing a path would put the file somewhere the pipeline won't look.
    """
    out = re.sub(r"\{\{#if _RCB\}\}.*?\{\{/if\}\}", uid, pattern, flags=re.S)
    out = out.replace("{{{File_MD5}}}", md5).replace("{{{_File_URL.filename_ext}}}", ext)
    if "{{" in out:
        sys.exit("Cannot resolve this catalog's url_pattern -- it uses template fields "
                 "this script does not understand:\n  %s" % pattern)
    return out


def do_upload(args):
    path = args.file
    name = os.path.basename(path)
    stem, ext = os.path.splitext(name)

    size = os.path.getsize(path)
    if size > MAX_PUT:
        sys.exit("%s is %.1f MB; this script does single-request uploads only "
                 "(limit %d MB).\nUse deriva-upload-cli for files this large."
                 % (path, size / 1e6, MAX_PUT // (1024 * 1024)))

    s = session()
    uid = whoami(s).rsplit("/", 1)[-1]

    # Take the upload rules from the catalog rather than hardcoding them: the
    # submitted-file namespace differs between dev and production, and this is
    # the same annotation the web UI obeys.
    asset = get(s, ASSET_ANNOTATION)
    allowed = tuple(asset.get("filename_ext_filter") or ALLOWED_EXT)
    if ext not in allowed:
        sys.exit("%s: this catalog only accepts %s. Other formats upload but then "
                 "fail in the validation pipeline." % (path, " / ".join(allowed)))

    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    md5_hex = digest.hexdigest()
    md5_b64 = base64.b64encode(digest.digest()).decode()

    title = args.title or "%s_%s" % (stem, uuid.uuid4().hex[:8])

    existing = get(s, "/entity/IHMV:Structure_mmCIF/File_MD5=%s&%s" % (md5_hex, mine(s)))
    if existing and not args.force:
        e = existing[0]
        print("Already submitted as %s (%s, %s). Use --force to submit again."
              % (e["RID"], e["Title"], e["Processing_Status"]), file=sys.stderr)
        print(e["RID"])
        return e["RID"]

    target = hatrac_target(asset["url_pattern"], uid, md5_hex, ext)

    # Hatrac is content-addressed here, so an identical object can be reused.
    head = s.head(HOST + target, timeout=60)
    if head.status_code == 200 and head.headers.get("Content-MD5") == md5_b64:
        url = head.headers["Content-Location"]
    else:
        with open(path, "rb") as fh:
            r = check(s.put(HOST + target + "?parents=true", data=fh, timeout=600, headers={
                "Content-MD5": md5_b64,
                "Content-Type": "application/octet-stream",
                "Content-Disposition": "filename*=UTF-8''" + quote(name),
            }))
        url = r.text.strip()
    if url.startswith(HOST):
        url = url[len(HOST):]

    row = {
        "Title": title,
        "File_URL": url,
        "File_Name": name,
        "File_Bytes": size,
        "File_MD5": md5_hex,
        "Description": args.description,
        "Processing_Status": "New",   # what tells the pipeline to pick it up
    }
    created = check(s.post(
        CAT + "/entity/IHMV:Structure_mmCIF?defaults=RID,RCT,RMT,RCB,RMB",
        json=[row], timeout=60)).json()
    print(created[0]["RID"])
    return created[0]["RID"]


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
    """The run log, stored with literal \\n escapes rather than real newlines."""
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


def poll_status(rids, verbose):
    """Fetch each RID's state once. Returns [(rid, row-or-None)] in request order."""
    s = session()   # rebuilt each poll so a long --wait refreshes its token
    match = "any(%s)" % ",".join(quote(r, safe="") for r in rids)
    cols = (LIST_COLS + ",Processing_Details") if verbose else "RID,Processing_Status"
    found = {r["RID"]: r for r in
             get(s, "/attribute/IHMV:Structure_mmCIF/RID=%s/%s" % (match, cols))}
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
    s = session()
    scope = "" if args.all else "/" + mine(s)
    cols = LIST_COLS + (",Processing_Details" if args.verbose else "")
    rows, notes = [], []
    for r in get(s, "/attribute/IHMV:Structure_mmCIF%s/%s@sort(RMT::desc::)" % (scope, cols)):
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

    s = session()
    check(s.put(CAT + "/attributegroup/IHMV:Structure_mmCIF/RID;Processing_Status",
                json=[{"RID": rid, "Processing_Status": args.to} for rid in rids], timeout=60))

    # Read back rather than echo the request, so the printed state is the truth.
    updated = poll_status(rids, False)
    if len(rids) == 1:
        print(updated[0][1]["Processing_Status"])
    else:
        emit_table(["RID", "STATUS"], [[r, row["Processing_Status"]] for r, row in updated])


def do_delete(args):
    rids = collect_rids(args)
    if not rids:
        sys.exit("no RIDs given (pass RIDs, or '-' to read them from stdin)")

    s = session()
    match = "any(%s)" % ",".join(quote(r, safe="") for r in rids)
    found = {r["RID"]: r for r in get(
        s, "/attribute/IHMV:Structure_mmCIF/RID=%s"
           "/RID,Title,Processing_Status,File_MD5,File_URL" % match)}
    if len(found) != len(rids):
        for rid in rids:
            if rid not in found:
                print("%s: unknown RID" % rid, file=sys.stderr)
        sys.exit(3)

    # Generated_File points at the structure by text with no FK, so nothing
    # cascades -- deleting the structure alone would strand its reports.
    reports = {}
    for r in get(s, "/attribute/IHMV:Generated_File/Structure_mmCIF=%s"
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
        check(s.delete(CAT + "/entity/IHMV:Generated_File/Structure_mmCIF=%s"
                       % quote(rid, safe=""), timeout=60))
        check(s.delete(CAT + "/entity/IHMV:Structure_mmCIF/RID=%s"
                       % quote(rid, safe=""), timeout=60))
        print(rid)

    if args.purge_file:
        for rid in rids:
            md5, url = found[rid]["File_MD5"], found[rid]["File_URL"]
            # Hatrac objects here are content-addressed, so one file can back
            # several records; only purge once nothing else references it.
            others = get(s, "/attribute/IHMV:Structure_mmCIF/File_MD5=%s/RID"
                            % quote(md5, safe=""))
            if others:
                print("  kept %s -- still used by %s"
                      % (url.rsplit("/", 1)[-1], ", ".join(r["RID"] for r in others)),
                      file=sys.stderr)
                continue
            check(s.delete(HOST + url.rsplit(":", 1)[0], timeout=60))
            print("  purged %s" % url.rsplit("/", 1)[-1], file=sys.stderr)


def do_download(args):
    rids = collect_rids(args)
    if not rids:
        sys.exit("no RIDs given (pass RIDs, or '-' to read them from stdin)")
    s = session()
    wanted = [REPORTS[k] for k in ("full", "summary") if getattr(args, k)] or list(REPORTS.values())
    # ERMrest takes a disjunction, so the whole batch is one round trip.
    match = "any(%s)" % ",".join(quote(r, safe="") for r in rids)

    targets = {r: [] for r in rids}
    if args.mmcif:
        for r in get(s, "/attribute/IHMV:Structure_mmCIF/RID=%s/RID,File_Name,File_URL" % match):
            # Report names already carry the RID; submitted files don't, so
            # prefix them when a batch could otherwise collide.
            name = r["File_Name"] if len(rids) == 1 else "%s_%s" % (r["RID"], r["File_Name"])
            targets[r["RID"]].append((name, r["File_URL"]))
    for r in get(s, "/attribute/IHMV:Generated_File/Structure_mmCIF=%s"
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
    host = os.environ.get("IHMV_HOST", host)
    catalog = os.environ.get("IHMV_CATALOG", catalog)
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

    q = add("login", help="authenticate with Globus")
    q.add_argument("--no-browser", action="store_true", help="just print the URL")
    q.set_defaults(func=do_login)

    q = add("upload", help="submit a structure for validation")
    q.add_argument("file", help="mmCIF file")
    q.add_argument("-t", "--title", help="default: <basename>_<uuid8>")
    q.add_argument("-d", "--description")
    q.add_argument("-f", "--force", action="store_true",
                   help="submit again even if this file was already submitted")
    q.set_defaults(func=do_upload)

    q = add("run", help="upload, then wait for validation to finish")
    q.add_argument("file", help="mmCIF file")
    q.add_argument("-t", "--title", help="default: <basename>_<uuid8>")
    q.add_argument("-d", "--description")
    q.add_argument("-f", "--force", action="store_true",
                   help="submit again even if this file was already submitted")
    q.add_argument("-v", "--verbose", action="store_true", help="print the full processing log")
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
    q.add_argument("-v", "--verbose", action="store_true",
                   help="print the full processing log")
    # A flag, not an optional value: `--wait 300` beside a variadic RID list
    # would otherwise read 300 as the interval and silently check nothing.
    q.add_argument("-w", "--wait", action="store_true",
                   help="poll until nothing is pending")
    q.add_argument("--interval", type=int, default=30, metavar="SECS",
                   help="seconds between polls (default 30)")
    q.set_defaults(func=do_status)

    q = add("set_status", help="ask the pipeline to (re)run these entries")
    q.add_argument("rids", nargs="*", metavar="RID",
                   help="one or more RIDs, or '-' to read them from stdin")
    q.add_argument("--rid", action="append", dest="rid_flags", metavar="RID",
                   help="same as a positional RID; may be repeated")
    q.add_argument("--to", required=True, choices=list(SETTABLE),
                   help="the state to set")
    q.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    q.set_defaults(func=do_set_status)

    q = add("delete", help="delete entries and their generated reports")
    q.add_argument("rids", nargs="*", metavar="RID",
                   help="one or more RIDs, or '-' to read them from stdin")
    q.add_argument("--rid", action="append", dest="rid_flags", metavar="RID",
                   help="same as a positional RID; may be repeated")
    q.add_argument("--purge-file", action="store_true",
                   help="also delete the submitted file from Hatrac, "
                        "unless another record shares it")
    q.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    q.set_defaults(func=do_delete)

    q = add("download", help="fetch entries' validation reports")
    q.add_argument("rids", nargs="*", metavar="RID",
                   help="one or more RIDs, or '-' to read them from stdin")
    q.add_argument("--rid", action="append", dest="rid_flags", metavar="RID",
                   help="same as a positional RID; may be repeated")
    q.add_argument("-o", "--outdir", default=".")
    q.add_argument("--full", action="store_true", help="full report only")
    q.add_argument("--summary", action="store_true", help="summary report only")
    q.add_argument("--mmcif", action="store_true", help="also fetch the submitted mmCIF")
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
