# Building a 9A9W entry

`assemble.py` turns raw files into a depositable IHM mmCIF, as a worked
example of preparing an entry. **See the main README for the workflow and what
the input files are** — this covers only what is worth knowing if you adapt
the code.

From the repository root:

```bash
pip install gemmi ihm
cd examples && python assemble.py
```

One entry, deliberately — not a general converter. It reads
`data/coordinates.cif`, `data/crosslinks.csv` and `data/restraints.csv`, and
writes `data/assembled.cif`. gemmi reads the coordinates; python-ihm describes
everything else.

The inputs are checked in, so this runs on a fresh clone. Only the output and
local scratch are ignored.

## Things that bite

- **`_entity.pdbx_description` is the only naming a coordinate file carries.**
  Without it the crosslink list's protein names have nothing to match against.
  gemmi's `make_mmcif_document()` drops it, so anything that rewrites the
  coordinates has to put it back.
- **The alphabets are keyed differently.** `DNAAlphabet` uses `DA`,
  `LPeptideAlphabet` the one-letter code. Look each component up by name
  first, then by letter.
- **Non-polymers have no `label_seq`.** The zincs vanish from the model if you
  skip atoms without one, and the write then fails with *"Assemblies reference
  asym IDs that don't have coordinates"*. A single-component entity's `seq_id`
  is always 1.
- **Ask the entity whether it is polymeric** before giving an asym unit a
  residue range — older gemmi assigns `label_seq` to non-polymers too, and
  `ihm` rejects a range on a ligand.
- **Chains have gaps.** Chain A holds 96 of its 139 residues, so the
  representation describes what was modelled, not the full sequence.

Needs current gemmi and python-ihm (tested with gemmi 0.7.5 / ihm 2.11);
gemmi 0.5.8 groups entities differently and the build fails.
