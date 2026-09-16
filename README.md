# ihmtools

Command-line tools for the [PDB-IHM](https://pdb-ihm.org) validation and
deposition systems. They talk to DERIVA's two REST APIs directly — ERMrest for
records, Hatrac for files — so the only dependency is `requests`.

```bash
pip install ihmtools
ihmv login          # once; Globus, via the browser
```

While only the TestPyPI pre-release exists, the second index is not optional:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ ihmtools
```

TestPyPI carries its own stale copy of `requests` (2.5.4.1, from 2015), so
without `--extra-index-url` pip installs that instead of the real one and every
command dies with `module 'collections' has no attribute 'MutableMapping'`.

Both commands default to the **dev** server; `--mode production` switches.

The examples below live in the repository, so clone it to run them:

```bash
git clone https://github.com/salilab/ihmtools.git
cd ihmtools
pip install -e .
```

## `ihmv` — validation catalog

```
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
ihmdep upload model.cif --image model.png      deposit an entry
ihmdep run model.cif                           deposit and block
ihmdep get_status                              list entries, newest first
ihmdep set_status 9-DXAM --to SUBMIT           DRAFT / DEPO / SUBMIT only
ihmdep download 9-DXAM                         fetch generated reports
ihmdep delete 9-DXAM                           pre-submit entries only
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
RID=$(ihmv upload model.cif)
ihmv get_status --wait "$RID" && ihmv download "$RID" -o reports/
```

`--wait` is a flag; `--interval SECS` changes the 30-second poll. They are
separate because a RID can be all digits, and an option that took an optional
value would read `--wait 300` as an interval rather than as RID 300.

Depositing several entries works the same way. `upload` prints nothing but
the RID on stdout, so the loop's output is the RID list, and every later
command reads it back with `-`.

`examples/G_1000003/` holds three entries from one PDB-IHM collection —
9A40, 9A6P and 9A7U, from "Modelling protein complexes with crosslinking mass
spectrometry and deep learning" — with their coordinates and images:

```bash
cd examples/G_1000003             # from the repository root

for id in 9A40 9A6P 9A7U; do
    ihmdep upload "$id.cif" --image "$id.png"
done > rids.txt

ihmdep get_status --wait - < rids.txt &&
ihmdep set_status --to SUBMIT --yes - < rids.txt &&
ihmdep get_status --wait - < rids.txt &&
ihmdep download --mmcif -o generated/ - < rids.txt
```

A failed upload prints no RID, so it drops out of the batch rather than
stopping it, and re-running the loop picks up the existing RIDs instead of
depositing twice. `get_status` exits non-zero if any entry errored, which
keeps a broken batch from being submitted. After SUBMIT the generated mmCIF
comes first; the validation PDFs arrive later, hence `--mmcif`.

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

## Notes

The two modules are deliberately self-contained — each can be copied out and
run on its own — which means they duplicate their auth and HTTP layers. A fix
to one must be applied to both.

Uploads follow the catalog's own `tag:isrd.isi.edu,2017:asset` annotation for
where files go and which extensions are accepted, which is what the web UI
obeys. Don't substitute the `bulk-upload` annotation that `deriva-upload-cli`
reads: on dev it points at a different Hatrac namespace.

## Tests

From the repository root:

```bash
pip install -e '.[test]'
pytest
```

Offline only — no network or credentials needed.
