# Examples

Three worked examples. **See the main README for the deposition workflow** —
this covers what is worth knowing if you adapt the code.

| | what it shows |
|---|---|
| [`9A9W/`](9A9W) | an **atomic** structure: 51729 atoms, DSSO crosslinks, a 3DEM map |
| [`9A8W/`](9A8W) | a **coarse-grained** IMP structure: 1663 spheres, SDA crosslinks, a 3DEM map |
| [`G_1000003/`](G_1000003) | three entries of one collection, for the batch deposit loop |

These are one way to make a deposition file, not the only one. PDB-IHM takes
any file that complies with the PDBx and IHM dictionaries ([IHMCIF][]),
however it was written; gemmi and python-ihm are only what these scripts
happen to use. Other code that writes IHMCIF:

- the [IMP deposition tutorial][imp-tutorial], which captures an IMP/PMI
  modeling script as it runs (`ProtocolOutput`) and adds the rest with
  python-ihm;
- [nup133][], a python-ihm script with helper modules that collects a study's
  SAXS profiles, 2DEM class averages, crosslinks and comparative models —
  the tutorial's own example of using python-ihm directly;
- [python-ihm's examples][ihm-examples], short scripts that each show one part
  of the library.

[IHMCIF]: https://github.com/ihmwg/IHMCIF
[imp-tutorial]: https://integrativemodeling.org/tutorials/deposition/
[nup133]: https://github.com/integrativemodeling/nup133/tree/main/outputs_foxs_ensemble_new/pdb-dev
[ihm-examples]: https://github.com/ihmwg/python-ihm/tree/main/examples

These scripts need [gemmi][] and [python-ihm][], which are **not** dependencies
of `ihmtools` — installing the package does not provide them:

```bash
pip install gemmi ihm

(cd 9A9W && python assemble.py)      # atomic
(cd 9A8W && python assemble.py)      # coarse-grained
```

[gemmi]: https://gemmi.readthedocs.io
[python-ihm]: https://python-ihm.readthedocs.io

Each script reads `data/` beside it and writes `data/assembled.cif`. One entry
each, deliberately — these are worked examples, not general converters.

Each directory has its own README that walks through the example step by
step — inputs, the code for each IHMCIF category, the rows it writes, checks
before upload, and what a real deposition adds: [`9A9W`](9A9W/README.md),
[`9A8W`](9A8W/README.md), [`G_1000003`](G_1000003/README.md).

## The two assemble scripts

They do the same job and are written to be read side by side. Everything that
differs follows from one thing: whether the model is atomic or coarse-grained.

| | 9A9W | 9A8W |
|---|---|---|
| model | 51729 atoms | 1663 spheres, **no atoms at all** |
| read with | `gemmi.read_structure()` | `_ihm_sphere_obj_site` directly — gemmi sees an empty structure |
| representation | atomic segments | multi-scale: rigid by-residue, flexible multi-residue beads |
| crosslinks | 51, all `ResidueCrossLink` | 251: **127 residue + 124 feature** |
| why two kinds | — | a bead spanning ten residues cannot be restrained at one of them |
| linker | DSSO, 30 Å | SDA, 22 Å, with `psi` and `sigma` from the IMP fit |
| protocol | five fitting and refinement steps | replica-exchange MC, then RMSD clustering |

Both reproduce their entry: identical sequences, identical coordinates
(atoms or spheres), identical crosslink tables row for row, and both validate
against `mmcif_ihm.dic` + `mmcif_pdbx_v50.dic`.

## Things that bite

- **`_entity.pdbx_description` is the only naming a coordinate file carries.**
  Without it the crosslink list's protein names have nothing to match against.
  gemmi's `make_mmcif_document()` drops it, so anything rewriting coordinates
  has to put it back.
- **The alphabets are keyed differently.** `DNAAlphabet` uses `DA`,
  `LPeptideAlphabet` the one-letter code. Look components up by name first.
- **Non-polymers have no `label_seq`** (9A9W's zincs). Skip atoms without one
  and they vanish, and the write fails with *"Assemblies reference asym IDs
  that don't have coordinates"*. A single-component entity's `seq_id` is 1.
- **Ask the entity whether it is polymeric** before giving an asym unit a
  residue range — older gemmi assigns `label_seq` to non-polymers too, and
  `ihm` rejects a range on a ligand.
- **A coarse-grained entry has an empty `_atom_site`.** `gemmi.read_structure()`
  returns zero chains rather than failing, so a script written for atoms
  silently produces nothing.

Needs current gemmi and python-ihm (tested with gemmi 0.7.5 / ihm 2.12);
gemmi 0.5.8 groups entities differently and the build fails.
