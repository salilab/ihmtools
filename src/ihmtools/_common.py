"""What `ihmv` and `ihmdep` share: the deriva-py layer and the CLI conventions.

The two front ends differ only in which catalog they talk to, which columns
they read, and which state machine they enforce. Everything else lives here.

Target resolution is module state, so `configure()` must run before
`connect()`. That is what `main()` below guarantees.
"""

import argparse
import json
import os
import re
import sys

import requests
from deriva.core import DerivaServer, HatracStore, get_credential, urlquote
from deriva.core.utils import hash_utils
from deriva.core.utils.globus_auth_utils import (DEFAULT_GLOBUS_CREDENTIAL_FILE,
                                                 DerivaJSONTokenStorage,
                                                 GlobusNativeLogin)
from fair_research_login.token_storage import REQUIRED_TOKEN_KEYS, TOKEN_GROUP_KEYS

GROUP_SIGNUP = "https://app.globus.org/groups/99da042e-64a6-11ea-ad5f-0ef992ed7ca1/about"

# Past this, hand the upload to Hatrac's chunked job API instead of one PUT.
CHUNK_THRESHOLD = 100 * 1024 * 1024

# Where these tools kept tokens before deriva-py did it for us.
LEGACY_TOKENS = os.path.expanduser("~/.config/ihmv/tokens.json")

# Set by configure() before any command runs. deriva-py takes the scheme and
# the bare hostname apart; URL is the two rejoined, for printed messages.
SCHEME, HOST, CATALOG_ID, URL = None, None, None, None


class NeedLogin(Exception):
    pass


# --------------------------------------------------------------------------
# target resolution
# --------------------------------------------------------------------------

def configure(args, modes, default_mode, env_prefix):
    """Resolve the target server: flags > --mode > environment > default."""
    global SCHEME, HOST, CATALOG_ID, URL
    host, catalog = modes[default_mode]
    host = os.environ.get(env_prefix + "_HOST", host)
    catalog = os.environ.get(env_prefix + "_CATALOG", catalog)
    if getattr(args, "mode", None):
        host, catalog = modes[args.mode]
    host = getattr(args, "host", None) or host
    catalog = getattr(args, "catalog", None) or catalog
    # --host has always accepted a whole URL, so split one apart rather than
    # rejecting it.
    SCHEME, _, rest = host.rpartition("://")
    SCHEME = SCHEME or "https"
    HOST = rest.strip("/")
    CATALOG_ID = str(catalog)
    URL = "%s://%s" % (SCHEME, HOST)


# --------------------------------------------------------------------------
# auth and http -- deriva-py owns all of it
# --------------------------------------------------------------------------

def _globus():
    return GlobusNativeLogin(hosts=[HOST])


def do_login(args):
    """Run Globus's native-app flow and let deriva-py store the tokens.

    no_local_server keeps this usable over ssh: Globus shows a code to paste
    back rather than redirecting to a port on this machine.
    """
    _globus().login(hosts=[HOST], no_local_server=True, no_browser=args.no_browser,
                    refresh_tokens=True)
    catalog, _ = connect()
    print("Logged in as %s" % whoami(catalog))
    print("Credentials saved to %s" % DEFAULT_GLOBUS_CREDENTIAL_FILE)


def do_logout(args):
    """Revoke the tokens at Globus and forget them.

    Deleting the file alone would leave a working token behind for anything
    that had already copied it, so revoke unless asked not to.
    """
    gnl = _globus()
    if not gnl.is_logged_in(hosts=[HOST]):
        print("not logged in to %s" % HOST, file=sys.stderr)
        return
    if args.local:
        gnl.client.token_storage.clear_tokens()
        print("forgot local credentials; they are still valid at Globus")
    else:
        gnl.logout(hosts=[HOST])
        print("revoked and removed credentials for %s" % HOST)


def adopt_legacy_tokens():
    """Import a pre-deriva-py token into deriva's store, once.

    Both stores hold grants for the same Globus native-app client, so the token
    is valid as-is; without this, upgrading would silently log everyone out.
    """
    if os.path.exists(DEFAULT_GLOBUS_CREDENTIAL_FILE) or not os.path.exists(LEGACY_TOKENS):
        return
    try:
        with open(LEGACY_TOKENS) as fh:
            entry = json.load(fh)
        # The store rejects a group carrying anything outside this set, and our
        # old format added expires_in and state.
        entry = {k: entry[k] for k in TOKEN_GROUP_KEYS if k in entry}
        if not REQUIRED_TOKEN_KEYS.issubset(entry):
            return
        DerivaJSONTokenStorage().write_tokens({entry["resource_server"]: entry})
    except (OSError, ValueError, KeyError):
        return          # unreadable or not a token; the caller will ask for a login
    print("Imported credentials from %s into %s"
          % (LEGACY_TOKENS, DEFAULT_GLOBUS_CREDENTIAL_FILE), file=sys.stderr)


def connect():
    """The catalog and the object store, authenticated.

    get_credential does the whole dance: scope discovery for the host, reading
    the token store, and refreshing an expired access token when a refresh
    token is on file.
    """
    adopt_legacy_tokens()
    cred = get_credential(HOST)
    if not cred or not (cred.get("bearer-token") or cred.get("cookie")):
        raise NeedLogin("no stored credentials for %s" % HOST)
    server = DerivaServer(SCHEME, HOST, cred)
    return server.connect_ermrest(CATALOG_ID), HatracStore(SCHEME, HOST, cred)


def check(call, *args, **kwargs):
    """Run one deriva-py call, turning its HTTPError into our own exits."""
    try:
        return call(*args, **kwargs)
    except requests.HTTPError as e:
        r = e.response
        if r is None:
            raise
        if r.status_code == 401:
            raise NeedLogin("server rejected the token")
        if r.status_code == 403:
            # Authentication worked, so this is a permission -- but on what? It
            # is the catalog for an ERMrest path and the object for a Hatrac
            # one, and those need different fixes, so name the URL.
            sys.exit("Access denied: %s\nYour identity is authenticated but not "
                     "permitted here.\nFor catalog access, request membership: %s"
                     % (r.url, GROUP_SIGNUP))
        sys.exit("%s %s\n%s" % (r.status_code, r.reason, r.text[:400]))


def get(catalog, path):
    return check(catalog.get, path).json()


def whoami(catalog):
    return check(catalog.get_authn_session).json()["client"]["id"]


def mine(catalog):
    return "RCB=" + urlquote(whoami(catalog))


def any_of(values):
    """An ERMrest disjunction, so a whole batch is one round trip."""
    return "any(%s)" % ",".join(urlquote(v) for v in values)


# --------------------------------------------------------------------------
# uploads
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
    """(hex, base64) md5 -- ERMrest stores the hex, Hatrac wants the base64."""
    h = hash_utils.compute_file_hashes(path, hashes=["md5"])["md5"]
    return h[0], h[1]


def prepare_asset(path, asset, allowed_fallback, refusal=None):
    """Validate and hash one input file.

    Purely local, and it must run before the dedupe lookup: otherwise a
    rejected file with a familiar md5 slips through as a match against an
    existing entry instead of being refused on its own merits.
    """
    if not os.path.isfile(path):
        sys.exit("%s: no such file" % path)
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1]
    allowed = tuple(asset.get("filename_ext_filter") or allowed_fallback)
    if ext not in allowed:
        sys.exit((refusal or "%s: only %s is accepted here.")
                 % (path, " / ".join(allowed)))

    md5_hex, md5_b64 = file_digest(path)
    return {"path": path, "name": name, "ext": ext, "size": os.path.getsize(path),
            "md5": md5_hex, "md5_b64": md5_b64}


def put_asset(store, prepared, asset, uid):
    """Upload a prepared file to Hatrac. Returns the columns describing it.

    put_loc HEADs first and reuses an object that already has this md5, which
    is what makes re-running an upload cheap; past CHUNK_THRESHOLD it switches
    to Hatrac's chunked job API rather than one very long PUT.
    """
    target = hatrac_target(asset, uid, prepared["md5"], prepared["ext"])
    url = check(
        store.put_loc, target, prepared["path"],
        md5=prepared["md5_b64"],
        content_type="application/octet-stream",
        content_disposition="filename*=UTF-8''" + urlquote(prepared["name"]),
        chunked=prepared["size"] > CHUNK_THRESHOLD,
        create_parents=True)
    return {"URL": url, "Name": prepared["name"],
            "MD5": prepared["md5"], "Bytes": prepared["size"]}


# --------------------------------------------------------------------------
# CLI conventions
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


def require_rids(args):
    """collect_rids, but for the commands where an empty list is a mistake."""
    rids = collect_rids(args)
    if not rids:
        sys.exit("no RIDs given (pass RIDs, or '-' to read them from stdin)")
    return rids


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

        def fmt(cols):
            # head[0] already carries the '#', and widths were measured with it,
            # so no extra adjustment: the label just sits one char right of data.
            return "  ".join(c.rjust(w) if i in right else c.ljust(w)
                             for i, (c, w) in enumerate(zip(cols, widths))).rstrip()
    else:
        def fmt(cols):
            return "\t".join(cols)

    print(fmt(head))
    for row, note in zip(cells, notes):
        print(fmt(row))
        if note:
            sys.stdout.flush()
            print(note, file=sys.stderr)


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


# --------------------------------------------------------------------------
# argument plumbing
# --------------------------------------------------------------------------

def add_rids(q, what="one or more RIDs, or '-' to read them from stdin"):
    """The positional/--rid pair every RID-taking subcommand repeats."""
    q.add_argument("rids", nargs="*", metavar="RID", help=what)
    q.add_argument("--rid", action="append", dest="rid_flags", metavar="RID",
                   help="same as a positional RID; may be repeated")
    return q


def add_interval(q, default=30):
    """Poll spacing, shared by the waiting commands."""
    q.add_argument("--interval", type=int, default=default, metavar="SECS",
                   help="seconds between polls (default %(default)s)")
    return q


def add_wait(q, default_interval=30):
    """--wait is a flag and --interval takes the value; they must stay apart.

    A RID can be all digits, so a --wait that took an optional value would read
    `--wait 300` as an interval, leave no RIDs, and silently list everything.
    """
    q.add_argument("-w", "--wait", action="store_true",
                   help="poll until nothing is pending")
    return add_interval(q, default_interval)


def build_parser(doc, modes, default_mode):
    """The shared parser skeleton. Returns (parser, add_subcommand)."""
    other = [m for m in modes if m != default_mode][0]
    # SUPPRESS so an unset option leaves no attribute behind; that lets these be
    # accepted both before and after the subcommand without the subparser's
    # defaults clobbering what the main parser already parsed.
    shared = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    g = shared.add_argument_group("server")
    g.add_argument("--mode", choices=sorted(modes),
                   help="%s = %s catalog %s (default); %s = %s catalog %s"
                        % ((default_mode,) + modes[default_mode]
                           + (other,) + modes[other]))
    g.add_argument("--host", metavar="HOST", help="override the server hostname")
    g.add_argument("--catalog", metavar="ID", help="override the catalog number")

    p = argparse.ArgumentParser(
        description=doc, formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[shared])
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, **kw):
        return sub.add_parser(name, parents=[shared], **kw)

    return p, add


def add_auth_commands(add, login_fn=do_login, logout_fn=do_logout):
    """login and logout are identical in both tools."""
    q = add("login", help="authenticate with Globus")
    q.add_argument("--no-browser", action="store_true", help="just print the URL")
    q.set_defaults(func=login_fn)

    q = add("logout", help="revoke the stored credentials and forget them")
    q.add_argument("--local", action="store_true",
                   help="only delete the local file; leave the token valid at Globus")
    q.set_defaults(func=logout_fn)


def dispatch(args):
    """Run one command, with the exit conventions both tools promise."""
    try:
        try:
            args.func(args)
        finally:
            # Flush while a BrokenPipeError can still be caught. Without this
            # the interpreter flushes at shutdown instead, where the only
            # possible outcome is "Exception ignored in: <_io.TextIOWrapper>".
            sys.stdout.flush()
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
        except (OSError, AttributeError, ValueError):
            pass        # stdout is not a real file descriptor; nothing to do
        sys.exit(128 + 13)
