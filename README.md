# ihmtools

Command-line tools for the [PDB-IHM](https://pdb-ihm.org) validation and
deposition systems. Built on [deriva-py][]: `ErmrestCatalog` for records,
`HatracStore` for files, `GlobusNativeLogin` for authentication.

```bash
pip install ihmtools
ihmv login          # once; prints a Globus URL, you paste the code back
ihmv whoami         # which account that was
ihmv logout         # revokes at Globus, then forgets the token
```

`login` stays in the terminal: it prints the URL rather than opening a browser
and never starts a local redirect server, so it works the same over ssh, in a
container, and on a desktop. That is also the safer default — the browser
already signed in to some Globus account is a good way to store credentials
for an account you did not mean. Pass `--browser` if you want it opened.

Credentials live in deriva-py's own store, `~/.deriva/globus-credential.json`,
so one login covers `ihmv`, `ihmdep` and the rest of the DERIVA client tools —
and logging out of any of them logs out of all of them. Tokens written by an
earlier `ihmtools` under `~/.config/ihmv/` are imported once, automatically.

Because that store is shared and long-lived, it is easy to be logged in as an
account you did not mean. `whoami` says which one, and `-v` adds the groups
that decide what it can reach:

```bash
$ ihmv whoami -v
you@example.org (442a6439-c239-4ff0-af85-d479bc676497)
  server   https://data-dev.pdb-ihm.org catalog 199
  full     Your Name <you@example.org>
  groups   pdb-reader, pdb-submitter, pdb-writer
  tokens   /home/you/.deriva/globus-credential.json
```

`login` prints the same block when it finishes, since the code you pasted says
nothing about which account it belonged to. Only the identity goes to stdout,
so `WHO=$(ihmv whoami)` works with `-v` too. An access-denied message names the
account as well — a 403 is more often the wrong one than a missing grant.

[deriva-py]: https://github.com/informatics-isi-edu/deriva-py

While only the TestPyPI pre-release exists, the second index is not optional:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ ihmtools
```

TestPyPI carries its own stale copy of `requests` (2.5.4.1, from 2015), so
without `--extra-index-url` pip installs that instead of the real one and every
command dies with `module 'collections' has no attribute 'MutableMapping'`.

Both tools default to the **production** server; `--mode dev` switches either
one. The worked examples below spell `--mode dev` out wherever they deposit a
real entry, and it is worth doing the same while you are finding your feet.

`ihmdep` allows only the two transitions a depositor drives — `DRAFT -> DEPO`
and `RECORD READY -> SUBMIT` — and deletes only `DRAFT` or `DEPO` entries.
Anything further along is deleted from the web interface, which has the
context to do it safely; these tools deliberately do not.

## `ihmv` — validation catalog

```
ihmv login                           authenticate with Globus, once
ihmv logout                          revoke those credentials and forget them
ihmv whoami                          which account those credentials belong to
ihmv upload model.cif                submit a structure for validation
ihmv run model.cif                   upload and block until it finishes
ihmv get_status                      list entries, newest first
ihmv get_status 2ZJ                  one word plus an exit code
ihmv set_status 2QJ --to Reprocess   ask the pipeline to run it again
ihmv download 2Y0 2XT                fetch validation reports
ihmv delete 2Y0                      remove a record and its reports
```

## `ihmdep` — deposition system

```
ihmdep login                                  authenticate with Globus, once
ihmdep logout                                 revoke those credentials and forget them
ihmdep whoami                                 which account those credentials belong to
ihmdep upload model.cif --image model.png     deposit an entry
ihmdep run model.cif                          deposit and block
ihmdep get_status                             list entries, newest first
ihmdep get_status 9-DXAM                      workflow; process, plus an exit code
ihmdep set_status 9-DXAM --to SUBMIT          DRAFT->DEPO, RECORD READY->SUBMIT
ihmdep download 9-DXAM                        fetch its generated mmCIF and reports
ihmdep delete 9-DXAM                          DRAFT or DEPO only
```

## Preparing an entry from raw files

`examples/` builds a depositable IHM mmCIF out of what an experimenter
actually has. There are two, covering the two kinds of integrative model:

| | |
|---|---|
| `examples/9A9W/` | **atomic** — USP7 bound to a nucleosome/p53 complex, 51729 atoms, DSSO crosslinks and a 3DEM map |
| `examples/9A8W/` | **coarse-grained** — human SNAPc-DNA from IMP, 1663 spheres, SDA crosslinks and a 3DEM map |

The examples need two libraries **`ihmtools` does not depend on** and
`pip install ihmtools` will not bring in — [gemmi][] for reading coordinates
and [python-ihm][] for writing the entry:

```bash
pip install gemmi ihm
```

Then, from the repository root:

```bash
# atomic
cd examples/9A9W
python assemble.py                                          # writes data/assembled.cif
ihmdep upload data/assembled.cif --image data/9A9W.png
```

```bash
# coarse-grained
cd examples/9A8W
python assemble.py                                          # writes data/assembled.cif
ihmdep upload data/assembled.cif --image data/9A8W.png
```

[gemmi]: https://gemmi.readthedocs.io
[python-ihm]: https://python-ihm.readthedocs.io

Each `assemble.py` reads the files in its own `data/` and derives everything
else with python-ihm: the entities and their sequences, one asym unit per
chain copy, the representation, the datasets, both restraints, the modelling
protocol, and the model itself.

| | 9A9W | 9A8W |
|---|---|---|
| the model | `coordinates.cif` — atoms | `model.cif` — spheres in `_ihm_sphere_obj_site` |
| measurements | `crosslinks.csv` — `id,protein1,residue1,protein2,residue2,linker` | same |
| restraints | `restraints.csv` — `crosslink_id,chain1,chain2` | plus a `granularity` column |
| image | `9A9W.png` | `9A8W.png` |

Both sets are checked in, so either example runs on a fresh clone. They were
recovered from the released entries: the coordinates by keeping only what
describes the model, the two crosslink tables from `_ihm_cross_link_list` and
`_ihm_cross_link_restraint`, and the images from `pdb-ihm.org/images/9a9w.png`
and `.../9a8w.png` (lowercase ids). For your own system they come from your
pipeline.

**Why the crosslinks are two files.** mmCIF keeps them apart and so does the
science. `crosslinks.csv` is what the experiment measured — protein and
residue, with no idea which copy. `restraints.csv` is what the modelling
actually restrained: the chain pair, for the subset used. For 9A9W those are
90 and 51. Of the 40 measurements left unrestrained, 25 have a residue that
isn't in the coordinates — it fell in a disordered gap, so there is no atom to
measure to — and the copy assignment can't be recovered from a measurement at
all: "H2B residue 24 to H2B residue 28" doesn't say *which* H2B, and there are
two of each histone and four p53.

**Why 9A8W needs a third column.** Its restraints are 127 by-residue and 124
by-feature, because a bead spanning several residues cannot be restrained at
one of them. That distinction exists only in a coarse-grained model, and no
measurement records it.

Both reproduce their entry: identical sequences, identical coordinates —
atoms for 9A9W, all 1663 spheres for 9A8W — both crosslink tables row for row,
and both validate against `mmcif_ihm.dic` + `mmcif_pdbx_v50.dic`.

## Scripting

`get_status` exits `0` done, `1` error, `2` pending, `3` unknown RID, so a
submit-and-wait loop is just:

```bash
RID=$(ihmv --mode dev upload model.cif)
ihmv --mode dev get_status --wait "$RID" &&
    ihmv --mode dev download "$RID" -o reports/
```

### Which status?

The deposition system keeps two: `Workflow_Status`, the stage an entry is at,
and `Process_Status`, how the last backend run went. A single RID reports
both, because neither answers the question on its own — `Success` is the same
word after the `DEPO` run and after the post-`SUBMIT` one:

```bash
$ ihmdep get_status 9-DXAM
RECORD READY; Success
```

Name one and you get it alone, which is the form to substitute into a script:

```bash
$ ihmdep get_status 9-DXAM --process
Success
$ ihmdep get_status 9-DXAM --workflow
RECORD READY
$ ihmdep get_status 9-DXAM --workflow --process
RECORD READY	Success
```

Asked for by name the output is tab-separated, like every other
machine-readable listing here; the unasked form uses `; ` because it is meant
to be read. With several RIDs both columns are printed already, and a flag
narrows the table to the one you asked for.

`ihmv` still prints a single word, because the validation catalog has only one
status column to report.

`--wait` is a flag; `--interval SECS` changes the 60-second poll. They are
separate because a RID can be all digits, and an option that took an optional
value would read `--wait 300` as an interval rather than as RID 300.

### Re-uploading the same file

Both tools dedupe on md5, so uploading a file you have already deposited
returns the existing RID instead of making a new entry. That is what you want
in production and the opposite of what you want while testing, so `upload` and
`run` take `--salt`:

```bash
ihmdep --mode dev upload model.cif --image model.png --salt
```

It uploads a timestamped copy — a trailing `# ihmtools-salt:` comment in the
mmCIF, a `tEXt` chunk in the PNG — so the name and the md5 are both new and
nothing has to be edited by hand. The copies go to a temporary directory,
whose path is printed. `--salt` takes no value, for the same reason `--wait`
does not: it sits beside a positional filename.

Use `--force` instead when you want the file you actually have on disk
deposited a second time, byte for byte.

Depositing several entries works the same way. `upload` prints nothing but
the RID on stdout, so the loop's output is the RID list, and every later
command reads it back with `-`.

`examples/G_1000003/` holds three entries from one PDB-IHM collection —
9A40, 9A6P and 9A7U, from "Modelling protein complexes with crosslinking mass
spectrometry and deep learning" — with their coordinates and images:

```bash
cd examples/G_1000003             # from the repository root

for id in 9A40 9A6P 9A7U; do
    ihmdep --mode dev upload "$id.cif" --image "$id.png"
done > rids.txt

ihmdep --mode dev get_status --wait - < rids.txt &&
ihmdep --mode dev set_status --to SUBMIT --yes - < rids.txt &&
ihmdep --mode dev get_status --wait - < rids.txt &&
ihmdep --mode dev download --mmcif -o generated/ - < rids.txt
```

`--mode dev` is spelled out because `ihmdep` now defaults to production, and a
worked example should not deposit to the live archive. `set_status --to SUBMIT`
requires every entry to be `RECORD READY`, which is what the preceding
`get_status --wait` establishes.

A failed upload prints no RID, so it drops out of the batch rather than
stopping it, and re-running the loop picks up the existing RIDs instead of
depositing twice. `get_status` exits non-zero if any entry errored, which
keeps a broken batch from being submitted. After SUBMIT the generated mmCIF
comes first and the validation PDFs arrive later, so `--mmcif` here asks for
the part that is ready.

`ihmdep download` with no flag takes everything the pipeline generated: the
mmCIF and both reports. `--mmcif`, `--full`, `--summary` and `--logs` each
narrow it to one kind and combine, so `--full --summary` is the two PDFs and
`--logs` on its own is the diagnostics from a failed run. (`ihmv download`
spells `--mmcif` differently, because the validation catalog generates no
mmCIF — there it fetches back the file you submitted.)

Listings are aligned on a terminal and **tab-separated when piped**, with a
`#`-prefixed header. Several columns contain spaces (`RECORD READY`, `Error:
processing uploaded mmCIF file`), so split on tabs rather than whitespace:

```bash
ihmdep get_status | awk -F'\t' '!/^#/ && $5 ~ /^Error/ {print $1}'
```

RIDs come from arguments, from `--rid` (repeatable), or from stdin via `-`.

Restraint data is not handled here. The guide's *Submission Step 3* covers
uploading it as CSV/TSV through the *Entry Related File* table in the web
interface.

## The official deposition guide

The [PDB-IHM Deposition and Data Harvesting User Guide][guide] is the
authoritative documentation: creating a Globus account and joining the
`pdb-submitter` group, the four submission steps in the web interface, how
restraint data is uploaded as CSV/TSV through the *Entry Related File* table,
accession codes and the release process.

Its last section documents the supported **bulk upload** route, which
`ihmdep` is an alternative to rather than a replacement for:

| | official route | `ihmdep` |
|---|---|---|
| tool | `deriva-upload-cli` | this package |
| layout | files must sit in `~/…/deriva/{globus_id}/entry/` | any path |
| pairing | `AB-AT.cif` and `AB-AT.png` must share a basename | `--image` names the file |
| login | `deriva-globus-auth-utils login --refresh` | `ihmdep login` |
| images | `.png` or `.jpg` | `.png` only |
| re-upload | same name or md5 is an error | reports the existing RID and stops |

Use whichever suits you. The official route is the one the PDB-IHM team
supports; if a deposit misbehaves, reproduce it with `deriva-upload-cli`
before reporting it.

[guide]: https://docs.google.com/document/d/1CM8-6PYqI0DvETeQEfoUpSFZ8BhLrvYnihLSK8ghVcI/edit

## Tests

From the repository root:

```bash
pip install -e '.[test]'
pytest
```

154 tests, offline — no network, no credentials, nothing written.

Two further tests drive a full round trip against the **dev** servers and are
deselected unless asked for:

```bash
pytest -m live          # ihmdep: upload + image -> wait -> RECORD READY -> SUBMIT -> download
                        # ihmv:   upload -> wait -> download both reports
```

They refuse to start unless every command would reach **dev**: the fixture
resolves `--mode dev` through the CLIs' own `configure()` and checks the host
and catalog it lands on, so neither an edit to the mode constant nor an
`IHMV_HOST`/`IHMDEP_HOST` in the environment can point them at production.

These need a login. Each run salts its own copy of
`examples/G_1000003/9A7U.cif` and `9A7U.png` through the same
`--salt` machinery described above, because both tools dedupe on md5 and
Hatrac is content-addressed: unsalted files would match the previous run
instead of exercising the upload path.

Nothing is cleaned up: each run leaves one record per tool on dev, and prints
both RIDs. A test that deleted its own entries would destroy the evidence you
want when it fails, and the deposition one could not be removed anyway, since
the round trip ends in SUBMIT.
