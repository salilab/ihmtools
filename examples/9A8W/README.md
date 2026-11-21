# Rebuilding PDB-IHM Entry 9A8W (Coarse-Grained IMP Model)

[`assemble.py`](assemble.py) rebuilds [PDB-IHM entry 9A8W][9a8w] (*"Integrative
structure of human SNAPc-DNA"*, Shah et al., *Nat. Commun.* 2025,
[10.1038/s41467-024-55553-8][paper]) from a stripped sphere-coordinate file and
two CSV tables of crosslinking-MS data.

[9a8w]: https://pdb-ihm.org/entry.html?9A8W
[paper]: https://doi.org/10.1038/s41467-024-55553-8

9A8W models the five-subunit human snRNA-activating protein complex (SNAPc1–5,
chains `A`–`E`) bound to a 20-bp U6 snRNA promoter DNA duplex (T and NT
strands, chains `F`–`G`). The structure was determined in IMP by replica-
exchange Monte Carlo sampling, restrained by photo-crosslinking MS with
succinimidyl 4,4'-azipentanoate (SDA; PRIDE `PXD053341`) and a 3DEM density map
(EMDB `EMD-50730`).

Compare [`../9A9W/`](../9A9W), which builds an atomic structure. Everything
that differs here follows from the model being **multi-scale coarse-grained**:

- There are **no atoms (`_atom_site` is absent)** — the model consists of 1663
  spheres in `_ihm_sphere_obj_site`, so `gemmi.read_structure()` sees an empty
  structure and `assemble.py` reads the mmCIF categories directly.
- Regions derived from experimental structures (`9FSO`, `7ZX8`, `7XUR`) or an
  AlphaFold/AlphaPulldown model (`ma-pgtjz`) are represented as **rigid
  1-residue spheres** (`ResidueSegment`), while unmodeled linker and tail
  regions are represented as **flexible multi-residue spheres**
  (`FeatureSegment`, up to 20 residues per bead).
- Because a multi-residue sphere has no single-residue coordinate, crosslink
  restraints carry a representation-dependent granularity
  (`_ihm_cross_link_restraint.model_granularity`): `by-residue`
  (`ResidueCrossLink`, 127 rows) when both endpoints lie in 1-residue spheres,
  and `by-feature` (`FeatureCrossLink`, 124 rows) whenever either endpoint lies
  in a multi-residue bead.

**What is reproduced and what is omitted.** Running `assemble.py` writes
`data/assembled.cif` (30 mmCIF categories), reproducing all 7 entity sequences,
all 1663 spheres, all 268 identified crosslinks in `_ihm_cross_link_list`, and
all 251 chain-resolved restraints in `_ihm_cross_link_restraint` row for row.
To keep the script short, it omits author names, citations, software records,
UniProt reference alignments, per-segment starting-model alignments
(`_ihm_starting_model_details`), and localization density file references
(`_ihm_localization_density_files`); [Polishing for a real
deposition](#polishing-for-a-real-deposition) gives self-contained snippets
for each using 9A8W's released values.

## Running the example

[`gemmi`][gemmi] and [`python-ihm`][ihm] are required by `assemble.py` (they
are not dependencies of `ihmtools` itself):

```bash
pip install gemmi ihm
cd examples/9A8W
python assemble.py
```

Output:

```text
wrote /.../examples/9A8W/data/assembled.cif
7 entities, 1663 spheres, 268 measurements, 251 restrained
```

[gemmi]: https://gemmi.readthedocs.io
[ihm]: https://python-ihm.readthedocs.io

## Inputs in `data/`

| file | contents |
|---|---|
| `data/model.cif` | 7 entities, chains `A`–`G`, and 1663 spheres |
| `data/crosslinks.csv` | 268 identified SDA crosslinks (entity level) |
| `data/restraints.csv` | 251 chain-resolved crosslink restraints |
| `data/9A8W.png` | preview image for `ihmdep upload --image` |

### `data/model.cif`

`data/model.cif` contains 7 categories (`_entity`, `_entity_poly`,
`_entity_poly_seq`, `_struct_asym`, `_chem_comp`, `_ihm_sphere_obj_site`, and
`_ihm_model_list`). `assemble.py` reads five of them:

| category | columns read | role in `assemble.py` |
|---|---|---|
| `_entity` | `id`, `pdbx_description` | maps IDs `1`–`7` to subunit names |
| `_entity_poly` | `entity_id`, `type` | DNA vs peptide alphabet |
| `_entity_poly_seq` | `entity_id`, `mon_id`, `num` | 1-based `seq_id` |
| `_struct_asym` | `id`, `entity_id` | maps chains `A`–`G` to entities |
| `_ihm_sphere_obj_site` | `asym_id`, `seq_id_*`, `Cartn_*` | sphere coords |

For each sphere in `_ihm_sphere_obj_site`, `assemble.py` reads `asym_id`,
`seq_id_begin`, `seq_id_end`, `Cartn_x`, `Cartn_y`, `Cartn_z`, and
`object_radius`.

### Sequence numbering (`seq_id` vs UniProt and template `auth_seq_id`)

Every residue number in `_ihm_sphere_obj_site` and `data/crosslinks.csv` is a
**1-based entity `seq_id`** (`_entity_poly_seq.num`), counting from the first
monomer of the modeled construct in `_entity_poly_seq`:

| entity | chain | `_entity.pdbx_description` | length | UniProt / offset |
|---|---|---|---|---|
| `1` | `A` | `... complex subunit 2` | 334 aa | `Q13487` `1–334` (`0`) |
| `2` | `B` | `... complex subunit 3` | 411 aa | `Q92966` `1–411` (`0`) |
| `3` | `C` | `... complex subunit 5` | 98 aa | `O75971` `1–98` (`0`) |
| `4` | `D` | `... complex subunit 1` | 368 aa | `Q16533` `1–368` (`0`) |
| `5` | `E` | `... complex subunit 4` | 1519 aa | `Q5SXM2` `2–1469` (`+39`) |
| `6` | `F` | `T strand` | 20 nt | `9FSO` `X` `-61..-42` (`-62`) |
| `7` | `G` | `NT strand` | 20 nt | `9FSO` `Y` `-15..+4` (`-16`) |

In entities `1`–`5`, `_entity.pdbx_description` starts with
`snRNA-activating protein `. Entity `5` (subunit 4, 1519 aa) has an unmodeled
40-residue N-terminal Twin-Strep-TEV tag (`MASWSHPQFEK...ENLYFQGSA`, `seq_id`
`1–40`, **no spheres** in `_ihm_sphere_obj_site`), `seq_id` `41–1508` aligned
to UniProt `Q5SXM2` residues `2–1469` (`seq_id - uniprot = +39`, or `+40`
relative to PDB `9FSO` chain `W`), and a C-terminal `Ala-His10` tag (`seq_id`
`1509–1519`).

Because `Entity.residue()` takes an entity `seq_id`, crosslink positions for
subunit 4 in `data/crosslinks.csv` include the 40-residue N-terminal tag offset
(for example, `residue2 = 41` in crosslink `174` is `Asp41` of the construct,
corresponding to `Asp2` of UniProt `Q5SXM2`).

### `data/crosslinks.csv`

268 rows of unique residue pairs identified by the crosslinking-MS search,
defined on entities without chain assignment:

| column | type | meaning |
|---|---|---|
| `id` | int (`1`–`268`) | key referenced by `restraints.csv` |
| `protein1` | string | first entity name (`_entity.pdbx_description`) |
| `residue1` | int | 1-based entity `seq_id` of the first endpoint |
| `protein2` | string | second entity name (`_entity.pdbx_description`) |
| `residue2` | int | 1-based entity `seq_id` of the second endpoint |
| `linker` | `SDA` | reagent name (not read by `assemble.py`) |

### `data/restraints.csv`

251 rows of chain-resolved distance restraints applied during IMP sampling:

| column | values | meaning |
|---|---|---|
| `crosslink_id` | int | foreign key to `crosslinks.csv` `id` |
| `chain1` | `A`–`E` | `_struct_asym.id` of the first restrained chain |
| `chain2` | `A`–`E` | `_struct_asym.id` of the second restrained chain |
| `granularity` | string | `by-residue` (127 rows) or `by-feature` (124 rows) |

## Step-by-step: from `assemble.py` to `data/assembled.cif`

### Reading `data/model.cif`

Because a coarse-grained entry has no `_atom_site` category,
`gemmi.read_structure()` returns an empty structure with zero chains. Instead,
`assemble.py` parses `data/model.cif` as a generic CIF block with
`gemmi.cif.read()` and extracts each category as a list of row dictionaries:

```python
block = gemmi.cif.read(MODEL).sole_block()


def category(name):
    """An mmCIF category as a list of row dicts."""
    columns = block.get_mmcif_category(name)
    if not columns:
        return []
    keys = list(columns)
    return [dict(zip(keys, row)) for row in zip(*(columns[k] for k in keys))]


descriptions = {r["id"]: r["pdbx_description"] for r in category("_entity")}
sequences = defaultdict(list)
for row in category("_entity_poly_seq"):
    sequences[row["entity_id"]].append(row["mon_id"])
poly_types = {r["entity_id"]: r["type"] for r in category("_entity_poly")}
```

### Molecular system: entities, asymmetric units, and assembly

Each unique polymer sequence becomes an `ihm.Entity` and each chain copy in
`_struct_asym` becomes an `ihm.AsymUnit`. Unlike 9A9W, 9A8W has no non-polymer
ligands and every entity is present in a single copy (7 entities → 7 chains
`A`–`G`). Note how `ihm.DNAAlphabet` is keyed by two-letter component IDs
(`DA`, `DC`, `DG`, `DT`) whereas `ihm.LPeptideAlphabet` is keyed by one-letter
codes, so `comps.get(name)` is tried before converting three-letter amino-acid
codes via `gemmi.find_tabulated_residue(name)`:

```python
entities = {}
for entity_id, components in sequences.items():
    alphabet = (ihm.DNAAlphabet if "deoxyribonucleotide" in poly_types[entity_id]
                else ihm.LPeptideAlphabet)
    comps = alphabet()._comps
    # DNAAlphabet is keyed by "DA", LPeptideAlphabet by the one-letter code.
    entities[entity_id] = ihm.Entity(
        [comps.get(name)
         or comps[gemmi.find_tabulated_residue(name).one_letter_code.upper()]
         for name in components],
        alphabet=alphabet, description=descriptions[entity_id])

asyms = {}
for row in category("_struct_asym"):
    entity = entities[row["entity_id"]]
    asyms[row["id"]] = ihm.AsymUnit(entity, details=entity.description,
                                    id=row["id"])

by_description = {e.description: e for e in entities.values()}
by_chain = {a.id: a for a in asyms.values()}

# CHANGE FOR YOUR ENTRY: system title, plus system.authors (_audit_author; see
# 9A9W/assemble.py), system.citations (_citation), system.software (_software),
# and entity.references (ihm.reference.UniProtSequence -> _struct_ref; see
# examples/G_1000003/*.cif for complete examples).
system = ihm.System(title="Integrative structure of human SNAPc-DNA")
system.entities.extend(entities.values())
system.asym_units.extend(asyms.values())

assembly = ihm.Assembly(list(asyms.values()), name="Modeled assembly")
```

This populates `_entry`, `_struct`, `_audit_conform`, `_chem_comp`, `_entity`,
`_entity_poly`, `_entity_poly_seq`, `_struct_asym`, `_pdbx_poly_seq_scheme`,
`_ihm_struct_assembly`, and `_ihm_struct_assembly_details` in
`data/assembled.cif` (rebuilt with `gemmi 0.7.5` / `ihm 2.12`):

```cif
data_model
_entry.id model
_struct.entry_id model
_struct.pdbx_model_details .
_struct.pdbx_structure_determination_methodology integrative
_struct.title 'Integrative structure of human SNAPc-DNA'
_audit_conform.dict_location https://raw.githubusercontent.com/ihmwg/IHMCIF/630553d/dist/mmcif_ihm.dic
_audit_conform.dict_name mmcif_ihm.dic
_audit_conform.dict_version 1.29
...
loop_
_entity.id
_entity.type
_entity.src_method
_entity.pdbx_description
_entity.formula_weight
_entity.pdbx_number_of_molecules
_entity.details
1 polymer man 'snRNA-activating protein complex subunit 2' 35598.427 1 .
2 polymer man 'snRNA-activating protein complex subunit 3' 46812.811 1 .
3 polymer man 'snRNA-activating protein complex subunit 5' 11343.487 1 .
4 polymer man 'snRNA-activating protein complex subunit 1' 43074.547 1 .
5 polymer man 'snRNA-activating protein complex subunit 4' 165125.820 1 .
6 polymer man 'T strand' 6154.003 1 .
7 polymer man 'NT strand' 6110.011 1 .
#
#
loop_
_struct_asym.id
_struct_asym.entity_id
_struct_asym.details
A 1 'snRNA-activating protein complex subunit 2'
B 2 'snRNA-activating protein complex subunit 3'
C 3 'snRNA-activating protein complex subunit 5'
D 4 'snRNA-activating protein complex subunit 1'
E 5 'snRNA-activating protein complex subunit 4'
F 6 'T strand'
G 7 'NT strand'
```

### Coarse-grained spheres and multi-scale representation

Each row in `_ihm_sphere_obj_site` covers a residue span `seq_id_begin..end` on
one chain. Where `begin == end`, a single residue is represented by its own
sphere; contiguous runs of 1-residue spheres form a rigid `ResidueSegment`
(`model_mode = rigid`, `model_granularity = by-residue`). Where `begin < end`
(for example, `1..10` and `11..20` on chain `A`), multiple residues are grouped
into one coarse sphere; contiguous stretches of equal-size coarse spheres form a
flexible `FeatureSegment` (`model_mode = flexible`,
`model_granularity = by-feature`, with `model_object_count` equal to the number
of spheres in the segment). Notice that chain `E` spheres start at
`seq_id_begin = 41`, so the unmodeled N-terminal tag (`seq_id` 1–40) is
automatically excluded from the representation segments:

```python
spheres = []
for row in category("_ihm_sphere_obj_site"):
    spheres.append((row["asym_id"], int(row["seq_id_begin"]), int(row["seq_id_end"]),
                    float(row["Cartn_x"]), float(row["Cartn_y"]), float(row["Cartn_z"]),
                    float(row["object_radius"])))

segments = []
for chain, asym in asyms.items():
    runs = [s for s in spheres if s[0] == chain]
    # contiguous stretches of equal bead size become one segment
    current = None
    for _, begin, end, *_rest in sorted(runs, key=lambda s: s[1]):
        size = end - begin + 1
        if current and current[2] == size and begin == current[1] + 1:
            current = (current[0], end, size, current[3] + 1)
            continue
        if current:
            segments.append((asym, *current))
        current = (begin, end, size, 1)
    if current:
        segments.append((asym, *current))

representation = ihm.representation.Representation([
    ihm.representation.ResidueSegment(asym(begin, end), rigid=True,
                                      primitive="sphere")
    if size == 1 else
    ihm.representation.FeatureSegment(asym(begin, end), rigid=False,
                                      primitive="sphere", count=count)
    for asym, begin, end, size, count in segments])
```

This writes 40 representation segments to `_ihm_entity_poly_segment` (plus 5
whole-chain segments `41`–`45` used by `_ihm_struct_assembly_details`), along
with `_ihm_model_representation` and `_ihm_model_representation_details`:

```cif
loop_
_ihm_entity_poly_segment.id
_ihm_entity_poly_segment.entity_id
_ihm_entity_poly_segment.seq_id_begin
_ihm_entity_poly_segment.seq_id_end
_ihm_entity_poly_segment.comp_id_begin
_ihm_entity_poly_segment.comp_id_end
1 1 1 20 MET GLY
2 1 21 28 PRO GLU
3 1 29 87 LYS GLN
...
22 4 355 368 SER HIS
23 5 41 120 ASP GLU
24 5 121 163 ASP GLY
...
39 6 1 20 DA DT
40 7 1 20 DA DT
...
loop_
_ihm_model_representation_details.id
_ihm_model_representation_details.representation_id
_ihm_model_representation_details.entity_id
_ihm_model_representation_details.entity_description
_ihm_model_representation_details.entity_asym_id
_ihm_model_representation_details.entity_poly_segment_id
_ihm_model_representation_details.model_object_primitive
_ihm_model_representation_details.starting_model_id
_ihm_model_representation_details.model_mode
_ihm_model_representation_details.model_granularity
_ihm_model_representation_details.model_object_count
_ihm_model_representation_details.description
1 1 1 'snRNA-activating protein complex subunit 2' A 1 sphere . flexible
by-feature 2 .
2 1 1 'snRNA-activating protein complex subunit 2' A 2 sphere . flexible
by-feature 1 .
3 1 1 'snRNA-activating protein complex subunit 2' A 3 sphere . rigid by-residue
. .
...
39 1 6 'T strand' F 39 sphere . rigid by-residue . .
40 1 7 'NT strand' G 40 sphere . rigid by-residue . .
```

### Input datasets

9A8W combines primary experimental data from PRIDE (`PXD053341`) and EMDB
(`EMD-50730`) with four structural templates from ModelArchive (`ma-pgtjz`) and
the PDB (`9FSO`, `7ZX8`, `7XUR`). Using `python-ihm`'s archive `Location`
classes sets `_ihm_dataset_list.database_hosted = YES` and populates
`_ihm_dataset_related_db_reference`:

```python
crosslink_data = ihm.dataset.CXMSDataset(ihm.location.PRIDELocation("PXD053341"))
em_data = ihm.dataset.EMDensityDataset(ihm.location.EMDBLocation("EMD-50730"))
alphafold_data = ihm.dataset.DeNovoModelDataset(
    ihm.location.ModelArchiveLocation("ma-pgtjz"))
pdb_9fso = ihm.dataset.PDBDataset(ihm.location.PDBLocation("9FSO"))
pdb_7zx8 = ihm.dataset.PDBDataset(ihm.location.PDBLocation("7ZX8"))
pdb_7xur = ihm.dataset.PDBDataset(ihm.location.PDBLocation("7XUR"))
```

```cif
loop_
_ihm_dataset_list.id
_ihm_dataset_list.data_type
_ihm_dataset_list.database_hosted
_ihm_dataset_list.details
1 'Crosslinking-MS data' YES .
2 '3DEM volume' YES .
3 'De Novo model' YES .
4 'Experimental model' YES .
5 'Experimental model' YES .
6 'Experimental model' YES .
#
#
loop_
_ihm_dataset_related_db_reference.id
_ihm_dataset_related_db_reference.dataset_list_id
_ihm_dataset_related_db_reference.db_name
_ihm_dataset_related_db_reference.accession_code
_ihm_dataset_related_db_reference.version
_ihm_dataset_related_db_reference.details
1 1 PRIDE PXD053341 . .
2 2 EMDB EMD-50730 . .
3 3 'MODEL ARCHIVE' ma-pgtjz . .
4 4 PDB 9FSO . .
5 5 PDB 7ZX8 . .
6 6 PDB 7XUR . .
```

The restraints below cite the crosslinking and 3DEM datasets, but nothing in
the script uses the four templates, and `python-ihm` writes only the datasets
that something cites. A dataset group collects all six, and the [sampling
step](#sampling-protocol-and-clustering-analysis) cites it; the released entry
likewise attaches a dataset group to that step:

```python
# python-ihm writes the datasets that something in the system cites: a
# restraint, a starting model, or a dataset group. Here the sampling step
# below cites this group, which is how the template models -- used by nothing
# else in this script -- get into the file. Nothing has to go in
# system.orphan_datasets, which is meant for data that nothing cites.
input_data = ihm.dataset.DatasetGroup(
    [crosslink_data, em_data, alphafold_data, pdb_9fso, pdb_7zx8, pdb_7xur])
```

```cif
loop_
_ihm_dataset_group.id
_ihm_dataset_group.name
_ihm_dataset_group.application
_ihm_dataset_group.details
1 . . .
#
#
loop_
_ihm_dataset_group_link.group_id
_ihm_dataset_group_link.dataset_list_id
1 1
1 2
1 3
1 4
1 5
1 6
```

### Crosslinks: identified crosslinks vs chain-resolved restraints

IHMCIF separates the **identified crosslinks** from the crosslinking-MS search
(`_ihm_cross_link_list`) from the **chain-resolved restraints** evaluated in
modeling (`_ihm_cross_link_restraint`):

- `data/crosslinks.csv` records 268 identified crosslinks on entities (186
  intra-protein, 81 inter-protein, and 1 homomeric self-link, `id=161`, at
  subunit 1 `K302–K302`). Because every entity in 9A8W is single-copy, there is
  no intra- vs inter-molecular copy ambiguity.
- `data/restraints.csv` records the 251 chain-resolved restraints (176
  intra-molecular, 75 inter-molecular) scored against a 22.0 Å upper bound with
  Bayesian nuisance parameters `psi = 0.25` and `sigma_1 = sigma_2 = 2.0`. The
  other 17 identified crosslinks (`id` `6`, `12`, `107`, `111`, `114`, `116`,
  `119`, `123`, `124`, `126`, `161`, `222`, `230`, `231`, `241`, `256`, `257`)
  are not restrained: 16 have an endpoint in the unmodeled N-terminal tag of
  subunit 4 (`seq_id` 1–40, which has no spheres in `_ihm_sphere_obj_site`),
  and `id=161` (`subunit 1 K302–K302`) is a same-residue self-link that implies
  an inter-molecular contact between two copies of subunit 1, which a
  single-copy model cannot satisfy.
- Restraint granularity (`_ihm_cross_link_restraint.model_granularity`) follows
  the representation of the two endpoints: 127 restraints have both endpoints
  in 1-residue spheres (`by-residue`, `ResidueCrossLink`), while 124 have at
  least one endpoint in a multi-residue sphere (`by-feature`,
  `FeatureCrossLink`: 50 with both endpoints in multi-residue beads and 74 with
  one 1-residue endpoint and one multi-residue endpoint).

```python
# CHANGE FOR YOUR ENTRY: SDA upper-bound distance in Angstroms
# (_ihm_cross_link_restraint.distance_threshold) and IMP's Bayesian nuisance
# parameters (_ihm_cross_link_restraint.psi, .sigma_1, .sigma_2).
CROSSLINK_DISTANCE = 22.0
CROSSLINK_PSI = 0.25
CROSSLINK_SIGMA = 2.0
...
# CHANGE FOR YOUR ENTRY: crosslinking reagent (ihm.cross_linkers.sda would
# also record its SMILES and InChI).
crosslink_restraint = ihm.restraint.CrossLinkRestraint(
    dataset=crosslink_data, linker=ihm.ChemDescriptor("SDA"))

experimental = {}
for row in csv.DictReader(open(CROSSLINKS)):
    link = ihm.restraint.ExperimentalCrossLink(
        by_description[row["protein1"]].residue(int(row["residue1"])),
        by_description[row["protein2"]].residue(int(row["residue2"])))
    experimental[row["id"]] = link
    crosslink_restraint.experimental_cross_links.append([link])

distance = ihm.restraint.UpperBoundDistanceRestraint(CROSSLINK_DISTANCE)
for row in csv.DictReader(open(RESTRAINTS)):
    link = experimental[row["crosslink_id"]]
    common = dict(experimental_cross_link=link, distance=distance,
                  asym1=by_chain[row["chain1"]], asym2=by_chain[row["chain2"]],
                  psi=CROSSLINK_PSI, sigma1=CROSSLINK_SIGMA,
                  sigma2=CROSSLINK_SIGMA)
    if row["granularity"] == "by-residue":
        crosslink_restraint.cross_links.append(
            ihm.restraint.ResidueCrossLink(**common))
    else:
        crosslink_restraint.cross_links.append(
            ihm.restraint.FeatureCrossLink(**common))
```

In `data/assembled.cif`, notice how crosslink `6` (touching `seq_id = 35` in
subunit 4's unmodeled tag) is present in `_ihm_cross_link_list` but skipped in
`_ihm_cross_link_restraint` (where restraint `5` maps to `group_id = 5` and
restraint `6` jumps to `group_id = 7`), and how restraint `10` (`group_id = 11`
at subunit 1 `His119–Lys314`, where `Lys314` lies in the 20-residue bead
`295..314`) has `model_granularity = by-feature`:

```cif
loop_
_ihm_chemical_component_descriptor.id
_ihm_chemical_component_descriptor.auth_name
_ihm_chemical_component_descriptor.chemical_name
_ihm_chemical_component_descriptor.common_name
_ihm_chemical_component_descriptor.smiles
_ihm_chemical_component_descriptor.smiles_canonical
_ihm_chemical_component_descriptor.inchi
_ihm_chemical_component_descriptor.inchi_key
1 SDA . . . . . .
...
loop_
_ihm_cross_link_list.id
_ihm_cross_link_list.group_id
_ihm_cross_link_list.entity_description_1
_ihm_cross_link_list.entity_id_1
_ihm_cross_link_list.seq_id_1
_ihm_cross_link_list.comp_id_1
_ihm_cross_link_list.entity_description_2
_ihm_cross_link_list.entity_id_2
_ihm_cross_link_list.seq_id_2
_ihm_cross_link_list.comp_id_2
_ihm_cross_link_list.linker_chem_comp_descriptor_id
_ihm_cross_link_list.linker_type
_ihm_cross_link_list.dataset_list_id
_ihm_cross_link_list.details
1 1 'snRNA-activating protein complex subunit 3' 2 155 PHE
'snRNA-activating protein complex subunit 3' 2 345 LYS 1 SDA 1 .
...
5 5 'snRNA-activating protein complex subunit 3' 2 281 HIS
'snRNA-activating protein complex subunit 4' 5 192 LYS 1 SDA 1 .
6 6 'snRNA-activating protein complex subunit 1' 4 313 LEU
'snRNA-activating protein complex subunit 4' 5 35 TYR 1 SDA 1 .
7 7 'snRNA-activating protein complex subunit 4' 5 432 LYS
'snRNA-activating protein complex subunit 4' 5 505 GLU 1 SDA 1 .
...
loop_
_ihm_cross_link_restraint.id
_ihm_cross_link_restraint.group_id
_ihm_cross_link_restraint.entity_id_1
_ihm_cross_link_restraint.asym_id_1
_ihm_cross_link_restraint.seq_id_1
_ihm_cross_link_restraint.comp_id_1
_ihm_cross_link_restraint.entity_id_2
_ihm_cross_link_restraint.asym_id_2
_ihm_cross_link_restraint.seq_id_2
_ihm_cross_link_restraint.comp_id_2
_ihm_cross_link_restraint.atom_id_1
_ihm_cross_link_restraint.atom_id_2
_ihm_cross_link_restraint.restraint_type
_ihm_cross_link_restraint.conditional_crosslink_flag
_ihm_cross_link_restraint.model_granularity
_ihm_cross_link_restraint.distance_threshold
_ihm_cross_link_restraint.psi
_ihm_cross_link_restraint.sigma_1
_ihm_cross_link_restraint.sigma_2
_ihm_cross_link_restraint.pseudo_site_flag
1 1 2 B 155 PHE 2 B 345 LYS . . 'upper bound' . by-residue 22.000 0.250 2.000
2.000 NO
...
5 5 2 B 281 HIS 5 E 192 LYS . . 'upper bound' . by-residue 22.000 0.250 2.000
2.000 NO
6 7 5 E 432 LYS 5 E 505 GLU . . 'upper bound' . by-residue 22.000 0.250 2.000
2.000 NO
...
9 10 4 D 193 VAL 4 D 209 LYS . . 'upper bound' . by-residue 22.000 0.250 2.000
2.000 NO
10 11 4 D 119 HIS 4 D 314 LYS . . 'upper bound' . by-feature 22.000 0.250 2.000
2.000 NO
```

### 3DEM restraint (and why `em_restraint.fits` is required)

`EM3DRestraint` links the 3DEM map dataset (`EMD-50730`) to the modeled
assembly. However, `ihm.dumper` writes one row in `_ihm_3dem_restraint` **per
model in `em_restraint.fits`** — if `em_restraint.fits[model]` is not populated
after `model` is instantiated, `_ihm_3dem_restraint` is omitted, without a
warning, from the output file even though `em_restraint` is in
`system.restraints`:

```python
# CHANGE FOR YOUR ENTRY: 3DEM fitting method (see em_restraint.fits below).
em_restraint = ihm.restraint.EM3DRestraint(
    dataset=em_data, assembly=assembly,
    fitting_method="Gaussian mixture models")
system.restraints.extend([crosslink_restraint, em_restraint])
...
# Required: _ihm_3dem_restraint is written only for models in
# em_restraint.fits (one row per fitted model).
em_restraint.fits[model] = ihm.restraint.EM3DRestraintFit()
```

```cif
loop_
_ihm_3dem_restraint.id
_ihm_3dem_restraint.dataset_list_id
_ihm_3dem_restraint.fitting_method
_ihm_3dem_restraint.fitting_method_citation_id
_ihm_3dem_restraint.struct_assembly_id
_ihm_3dem_restraint.map_segment_flag
_ihm_3dem_restraint.number_of_gaussians
_ihm_3dem_restraint.model_id
_ihm_3dem_restraint.cross_correlation_coefficient
_ihm_3dem_restraint.details
1 2 'Gaussian mixture models' . 1 . . 1 . .
```

### Sampling protocol and clustering analysis

`ihm.protocol.Protocol` and `ihm.analysis.Analysis` record the replica-exchange
Monte Carlo sampling (`0 -> 320000` models) and the RMSD clustering step
(`320000 -> 8260` models):

```python
protocol = ihm.protocol.Protocol(name="Modeling")
protocol.steps.append(ihm.protocol.Step(
    assembly=assembly, dataset_group=input_data, name="Sampling",
    method="Replica exchange monte carlo",
    num_models_begin=0, num_models_end=320000))

analysis = ihm.analysis.Analysis()
analysis.steps.append(ihm.analysis.ClusterStep(
    feature="RMSD", num_models_begin=320000, num_models_end=8260))
protocol.analyses.append(analysis)
# The model below cites this protocol, and that is what writes it.
```

```cif
loop_
_ihm_modeling_protocol.id
_ihm_modeling_protocol.protocol_name
_ihm_modeling_protocol.num_steps
_ihm_modeling_protocol.details
1 Modeling 1 .
#
#
loop_
_ihm_modeling_protocol_details.id
_ihm_modeling_protocol_details.protocol_id
_ihm_modeling_protocol_details.step_id
_ihm_modeling_protocol_details.struct_assembly_id
_ihm_modeling_protocol_details.dataset_group_id
_ihm_modeling_protocol_details.step_name
_ihm_modeling_protocol_details.step_method
_ihm_modeling_protocol_details.num_models_begin
_ihm_modeling_protocol_details.num_models_end
_ihm_modeling_protocol_details.multi_scale_flag
_ihm_modeling_protocol_details.multi_state_flag
_ihm_modeling_protocol_details.ordered_flag
_ihm_modeling_protocol_details.ensemble_flag
_ihm_modeling_protocol_details.software_id
_ihm_modeling_protocol_details.script_file_id
_ihm_modeling_protocol_details.description
1 1 1 1 1 Sampling 'Replica exchange monte carlo' 0 320000 NO NO NO YES . . .
#
#
loop_
_ihm_modeling_post_process.id
_ihm_modeling_post_process.protocol_id
_ihm_modeling_post_process.analysis_id
_ihm_modeling_post_process.step_id
_ihm_modeling_post_process.type
_ihm_modeling_post_process.feature
_ihm_modeling_post_process.num_models_begin
_ihm_modeling_post_process.num_models_end
_ihm_modeling_post_process.struct_assembly_id
_ihm_modeling_post_process.dataset_group_id
_ihm_modeling_post_process.software_id
_ihm_modeling_post_process.script_file_id
_ihm_modeling_post_process.details
1 1 1 1 cluster RMSD 320000 8260 . . . . .
```

### Sphere model, model group, state, and ensemble

Instead of overriding `get_atoms()`, a coarse-grained `ihm.model.Model`
overrides `get_spheres()` to yield `ihm.model.Sphere` objects. Placing `model`
inside a `ModelGroup`, `State`, and `StateGroup`, and attaching an
`ihm.model.Ensemble` (`num_models=5766`), writes `_ihm_model_list`,
`_ihm_model_group`, `_ihm_model_group_link`, `_ihm_sphere_obj_site`, and
`_ihm_ensemble_info` (and sets `_ihm_modeling_protocol_details.ensemble_flag`
to `YES`):

```python
class Model(ihm.model.Model):
    """A sphere model: get_spheres() rather than get_atoms().

    Writes _ihm_sphere_obj_site (1663 rows) and _ihm_model_list.
    """

    def get_spheres(self):
        for chain, begin, end, x, y, z, radius in spheres:
            yield ihm.model.Sphere(
                asym_unit=asyms[chain], seq_id_range=(begin, end),
                x=x, y=y, z=z, radius=radius)


model = Model(assembly=assembly, protocol=protocol,
              representation=representation, name="Best scoring model")
# Required: _ihm_3dem_restraint is written only for models in
# em_restraint.fits (one row per fitted model).
em_restraint.fits[model] = ihm.restraint.EM3DRestraintFit()

# Writes _ihm_model_group and _ihm_model_group_link.
group = ihm.model.ModelGroup([model], name="Cluster 0")
system.state_groups.append(ihm.model.StateGroup([ihm.model.State([group])]))

# CHANGE FOR YOUR ENTRY: ensemble size and clustering method.
# Writes _ihm_ensemble_info and sets
# _ihm_modeling_protocol_details.ensemble_flag = YES.
system.ensembles.append(ihm.model.Ensemble(
    model_group=group, num_models=5766, name="Cluster 0",
    clustering_method="Density based threshold-clustering",
    clustering_feature="RMSD"))
```

```cif
loop_
_ihm_model_list.model_id
_ihm_model_list.model_name
_ihm_model_list.assembly_id
_ihm_model_list.protocol_id
_ihm_model_list.representation_id
1 'Best scoring model' 1 1 1
#
#
loop_
_ihm_model_group.id
_ihm_model_group.name
_ihm_model_group.details
1 'Cluster 0' .
#
#
loop_
_ihm_model_group_link.group_id
_ihm_model_group_link.model_id
1 1
#
#
loop_
_ihm_sphere_obj_site.id
_ihm_sphere_obj_site.entity_id
_ihm_sphere_obj_site.seq_id_begin
_ihm_sphere_obj_site.seq_id_end
_ihm_sphere_obj_site.asym_id
_ihm_sphere_obj_site.Cartn_x
_ihm_sphere_obj_site.Cartn_y
_ihm_sphere_obj_site.Cartn_z
_ihm_sphere_obj_site.object_radius
_ihm_sphere_obj_site.rmsf
_ihm_sphere_obj_site.model_id
1 1 1 10 A 215.406 226.091 72.181 5.992 . 1
2 1 11 20 A 212.989 213.802 57.657 5.992 . 1
3 1 21 28 A 207.046 209.176 67.218 5.562 . 1
4 1 29 29 A 210.541 202.426 77.173 3.047 . 1
...
1663 7 20 20 G 180.389 200.645 122.661 2.516 . 1
#
#
loop_
_ihm_ensemble_info.ensemble_id
_ihm_ensemble_info.ensemble_name
_ihm_ensemble_info.post_process_id
_ihm_ensemble_info.model_group_id
_ihm_ensemble_info.ensemble_clustering_method
_ihm_ensemble_info.ensemble_clustering_feature
_ihm_ensemble_info.num_ensemble_models
_ihm_ensemble_info.num_ensemble_models_deposited
_ihm_ensemble_info.ensemble_precision_value
_ihm_ensemble_info.ensemble_file_id
_ihm_ensemble_info.details
_ihm_ensemble_info.model_group_superimposed_flag
_ihm_ensemble_info.sub_sample_flag
_ihm_ensemble_info.sub_sampling_type
1 'Cluster 0' . 1 'Density based threshold-clustering' RMSD 5766 1 . . . . NO .
```

### Writing and reading back

`ihm.dumper.write()` serializes `system` to `data/assembled.cif`, and
`ihm.reader.read()` parses it back to verify that entities, spheres, identified
crosslinks, and restraints round-trip cleanly:

```python
with open(OUTPUT, "w") as fh:
    ihm.dumper.write(fh, [system])
print("wrote %s" % OUTPUT)

# Read back with python-ihm as a quick local check. Before depositing with
# ihmdep, also validate against the PDBx + IHM dictionaries or run:
#   ihmv --mode dev run data/assembled.cif
with open(OUTPUT) as fh:
    check, = ihm.reader.read(fh)
crosslinks = check.restraints[0]
built = [m for sg in check.state_groups for st in sg for g in st for m in g][0]
print("%d entities, %d spheres, %d measurements, %d restrained"
      % (len(check.entities), len(built._spheres),
         sum(len(g) for g in crosslinks.experimental_cross_links),
         len(crosslinks.cross_links)))
```

## Checking before upload

First, validate `data/assembled.cif` locally against the PDBx and IHM
dictionaries — **fetched fresh**:

```python
import urllib.request, ihm.dictionary
B = "https://mmcif.wwpdb.org/dictionaries/ascii/"
d = ihm.dictionary.read(urllib.request.urlopen(B + "mmcif_pdbx_v50.dic"))
d += ihm.dictionary.read(urllib.request.urlopen(B + "mmcif_ihm_ext.dic"))
d.validate(open("data/assembled.cif"))   # raises ValidatorError, or is silent
```

`python-ihm` tracks the dictionaries closely, so a cached copy goes stale and
rejects valid output: `ihm 2.12` writes
`_pdbx_poly_seq_scheme.ihm_model_id_list`, which a dictionary cached before
October 2026 does not define.

Next, run the server's validation pipeline via `ihmv` and inspect the generated
validation PDFs before depositing with `ihmdep` (see the [main
README](../../README.md) for authentication and the full deposition workflow):

```bash
ihmv --mode dev run data/assembled.cif
ihmv --mode dev download <RID> -o reports/
ihmdep --mode dev upload data/assembled.cif --image data/9A8W.png
```

## Polishing for a real deposition

The snippets below add the metadata present in the [released 9A8W
entry](https://pdb-ihm.org/cif/9A8W.cif) that `assemble.py` leaves out for
brevity. Placed before `ihm.dumper.write(fh, [system])`, all six snippets run
together and validate cleanly against `mmcif_pdbx_v50.dic` +
`mmcif_ihm_ext.dic`.

### Crosslinker chemistry (`ihm.cross_linkers.sda`)

`ihm.ChemDescriptor("SDA")` writes only the short name and leaves the chemical
structure fields blank (`1 SDA . . . . . .`). Using the built-in descriptor
from `ihm.cross_linkers` populates the chemical name, SMILES, InChI, and
InChIKey:

```python
import ihm.cross_linkers
crosslink_restraint.linker = ihm.cross_linkers.sda
```

```cif
loop_
_ihm_chemical_component_descriptor.id
_ihm_chemical_component_descriptor.auth_name
_ihm_chemical_component_descriptor.chemical_name
_ihm_chemical_component_descriptor.common_name
_ihm_chemical_component_descriptor.smiles
_ihm_chemical_component_descriptor.smiles_canonical
_ihm_chemical_component_descriptor.inchi
_ihm_chemical_component_descriptor.inchi_key
1 SDA "succinimidyl 4,4'-azipentanoate" . CC1(N=N1)CCC(ON2C(CCC2=O)=O)=O .
1S/C9H11N3O4/c1-9(10-11-9)5-4-8(15)16-12-6(13)2-3-7(12)14/h2-5H2,1H3
' SYYLQNPWAPHRFV-UHFFFAOYSA-N'
```

### Entry authors and primary citation (`_audit_author`, `_citation`)

```python
system.authors.extend([
    "Shah, S.Z.", "Perry, T.N.", "Graziadei, A.", "Cecatiello, V.",
    "Kaliyappan, T.", "Misiaszek, A.D.", "Mueller, C.W.",
    "Ramsay, E.P.", "Vannini, A.",
])
system.citations.append(ihm.Citation(
    pmid="39747245",
    title="Structural insights into distinct mechanisms of RNA "
          "polymerase II and III recruitment to snRNA promoters",
    journal="Nat Commun", volume=None, page_range=None, year=2025,
    authors=system.authors,
    doi="10.1038/s41467-024-55553-8"))
```

```cif
loop_
_citation.id
_citation.title
_citation.journal_abbrev
_citation.journal_volume
_citation.page_first
_citation.page_last
_citation.year
_citation.journal_id_ASTM
_citation.journal_id_CSD
_citation.journal_id_ISSN
_citation.pdbx_database_id_PubMed
_citation.pdbx_database_id_DOI
1
'Structural insights into distinct mechanisms of RNA polymerase II and III recruitment to snRNA promoters'
'Nat Commun' . . . 2025 . . . 39747245 10.1038/s41467-024-55553-8
#
#
loop_
_citation_author.citation_id
_citation_author.name
_citation_author.ordinal
1 'Shah, S.Z.' 1
1 'Perry, T.N.' 2
...
1 'Vannini, A.' 9
...
loop_
_audit_author.name
_audit_author.pdbx_ordinal
'Shah, S.Z.' 1
'Perry, T.N.' 2
...
'Vannini, A.' 9
```

### Software (`_software`)

```python
imp_pmi = ihm.Software(
    name="IMP PMI module", classification="integrative model building",
    description="integrative model building",
    location="https://integrativemodeling.org", version="2.20.0")
alphapulldown = ihm.Software(
    name="AlphaPulldown", classification="structure prediction",
    description="a customized implementation of AlphaFold-Multimer "
                "designed for customizable high-throughput screening "
                "of protein-protein interactions.",
    location="https://github.com/KosinskiLab/AlphaPulldown",
    version="0.30.7")
system.software.extend([imp_pmi, alphapulldown])
protocol.steps[0].software = imp_pmi
```

```cif
loop_
_software.pdbx_ordinal
_software.name
_software.classification
_software.description
_software.version
_software.type
_software.location
_software.citation_id
1 'IMP PMI module' 'integrative model building' 'integrative model building'
2.20.0 program https://integrativemodeling.org .
2 AlphaPulldown 'structure prediction'
'a customized implementation of AlphaFold-Multimer designed for customizable high-throughput screening of protein-protein interactions.'
0.30.7 program https://github.com/KosinskiLab/AlphaPulldown .
```

### UniProt reference sequences (`_struct_ref`, `_struct_ref_seq`)

Subunits 2, 3, 5, and 1 (entities `1`–`4`) match their canonical UniProt
sequences over their full length. Subunit 4 (entity `5`, 1519 aa) carries a
40-residue N-terminal tag and an 11-residue C-terminal tag, so an explicit
`ihm.reference.Alignment` maps construct `seq_id` `41..1508` to UniProt
`Q5SXM2` residues `2..1469`:

```python
import ihm.reference

for eid, code, acc in [
    ("1", "SNPC2_HUMAN", "Q13487"),
    ("2", "SNPC3_HUMAN", "Q92966"),
    ("3", "SNPC5_HUMAN", "O75971"),
    ("4", "SNPC1_HUMAN", "Q16533"),
]:
    seq = "".join(c.code_canonical for c in entities[eid].sequence)
    entities[eid].references.append(ihm.reference.UniProtSequence(
        db_code=code, accession=acc, sequence=seq))

snap4_seq = "".join(c.code_canonical for c in entities["5"].sequence[40:1508])
ref5 = ihm.reference.UniProtSequence(
    db_code="SNPC4_HUMAN", accession="Q5SXM2", sequence="M" + snap4_seq)
ref5.alignments.append(ihm.reference.Alignment(
    db_begin=2, db_end=1469, entity_begin=41, entity_end=1508))
entities["5"].references.append(ref5)
```

```cif
loop_
_struct_ref.id
_struct_ref.entity_id
_struct_ref.db_name
_struct_ref.db_code
_struct_ref.pdbx_db_accession
_struct_ref.pdbx_align_begin
_struct_ref.pdbx_seq_one_letter_code
_struct_ref.details
1 1 UNP SNPC2_HUMAN Q13487 1
;MKPPPRRRAAPARYLGEVTGPATWSAREKRQLVRLLQARQGQPEPDATELARELRGRSEAEIRVFLQQLK
...
5 5 UNP SNPC4_HUMAN Q5SXM2 2
;DVDAEREKITQEIKELERILDPGSSGSHVEISESSLESDSEADSLPSEDLDPADPPISEEERWGEASNDE
...
#
#
loop_
_struct_ref_seq.align_id
_struct_ref_seq.ref_id
_struct_ref_seq.seq_align_beg
_struct_ref_seq.seq_align_end
_struct_ref_seq.db_align_beg
_struct_ref_seq.db_align_end
1 1 1 334 1 334
2 2 1 411 1 411
3 3 1 98 1 98
4 4 1 368 1 368
5 5 41 1508 2 1469
```

### Starting models (`_ihm_starting_model_details`)

In `assemble.py`, `ma-pgtjz`, `9FSO`, `7ZX8`, and `7XUR` are cited only by the
sampling step's dataset group, which does not say which part of the model each
one supplied. Wrapping each template-derived region in
`ihm.startmodel.StartingModel` and attaching it to the corresponding
`ResidueSegment` populates `_ihm_starting_model_details` and sets
`_ihm_model_representation_details.starting_model_id`:

```python
import ihm.startmodel

sm_b = ihm.startmodel.StartingModel(
    asym_unit=asyms["B"](27, 411), dataset=pdb_9fso, asym_id="Z", offset=0)
sm_e = ihm.startmodel.StartingModel(
    asym_unit=asyms["E"](182, 416), dataset=pdb_9fso, asym_id="W", offset=40)
sm_f = ihm.startmodel.StartingModel(
    asym_unit=asyms["F"](1, 20), dataset=pdb_9fso, asym_id="X", offset=-62)
for seg in representation:
    r = seg.asym_unit
    if (r.asym.id, r.seq_id_range) == ("B", (27, 411)):
        seg.starting_model = sm_b
    elif (r.asym.id, r.seq_id_range) == ("E", (182, 416)):
        seg.starting_model = sm_e
    elif (r.asym.id, r.seq_id_range) == ("F", (1, 20)):
        seg.starting_model = sm_f
```

```cif
loop_
_ihm_starting_model_details.starting_model_id
_ihm_starting_model_details.entity_id
_ihm_starting_model_details.entity_description
_ihm_starting_model_details.asym_id
_ihm_starting_model_details.entity_poly_segment_id
_ihm_starting_model_details.starting_model_source
_ihm_starting_model_details.starting_model_auth_asym_id
_ihm_starting_model_details.starting_model_sequence_offset
_ihm_starting_model_details.dataset_list_id
_ihm_starting_model_details.description
1 2 'snRNA-activating protein complex subunit 3' B 1 'experimental model' Z 0 4
.
2 5 'snRNA-activating protein complex subunit 4' E 2 'experimental model' W 40 4
.
3 6 'T strand' F 3 'experimental model' X -62 4 .
```

### Multi-scale flag and localization densities

Setting `multi_scale = True` on `protocol.steps[0]` sets
`_ihm_modeling_protocol_details.multi_scale_flag = YES`. Linking
`ensemble.post_process = analysis.steps[0]` and
adding `ihm.model.LocalizationDensity` objects pointing to 9A8W's Zenodo
archive (`10.5281/zenodo.14746890`) populates `_ihm_external_reference_info`,
`_ihm_external_files`, and `_ihm_localization_density_files`:

```python
protocol.steps[0].multi_scale = True
ensemble = system.ensembles[0]
ensemble.post_process = analysis.steps[0]

repo = ihm.location.Repository(
    doi="10.5281/zenodo.14746890",
    url="https://zenodo.org/records/14746890/files/SNAPCfiles.zip")
for fname, chain, end in [
    ("LPD_SNAP1domain1.mrc", "D", 368),
    ("LPD_SNAP1domain2.mrc", "D", 368),
    ("LPD_SNAP2_SNAP4domain.mrc", "A", 334),
    ("LPD_Wing2.mrc", "C", 98),
    ("LPD_SNAP3domain1.mrc", "B", 411),
    ("LPD_SNAP4domain1.mrc", "E", 1519),
]:
    loc = ihm.location.OutputFileLocation(
        path=fname, repo=repo, details="Localization density")
    ensemble.densities.append(ihm.model.LocalizationDensity(
        file=loc, asym_unit=asyms[chain](1, end)))
```

```cif
loop_
_ihm_external_reference_info.reference_id
_ihm_external_reference_info.reference_provider
_ihm_external_reference_info.reference_type
_ihm_external_reference_info.reference
_ihm_external_reference_info.refers_to
_ihm_external_reference_info.associated_url
_ihm_external_reference_info.details
1 Zenodo DOI 10.5281/zenodo.14746890 Archive
https://zenodo.org/records/14746890/files/SNAPCfiles.zip .
#
#
loop_
_ihm_external_files.id
_ihm_external_files.reference_id
_ihm_external_files.file_path
_ihm_external_files.content_type
_ihm_external_files.file_format
_ihm_external_files.file_size_bytes
_ihm_external_files.details
1 1 LPD_SNAP1domain1.mrc 'Modeling or post-processing output' . .
'Localization density'
...
6 1 LPD_SNAP4domain1.mrc 'Modeling or post-processing output' . .
'Localization density'
...
loop_
_ihm_modeling_protocol_details.id
...
1 1 1 1 1 Sampling 'Replica exchange monte carlo' 0 320000 YES NO NO YES 1 . .
#
#
loop_
_ihm_ensemble_info.ensemble_id
...
1 'Cluster 0' 1 1 'Density based threshold-clustering' RMSD 5766 1 . . . . NO .
#
#
loop_
_ihm_localization_density_files.id
_ihm_localization_density_files.file_id
_ihm_localization_density_files.ensemble_id
_ihm_localization_density_files.entity_id
_ihm_localization_density_files.asym_id
_ihm_localization_density_files.entity_poly_segment_id
1 1 1 4 D 44
2 2 1 4 D 44
3 3 1 1 A 41
4 4 1 3 C 43
5 5 1 2 B 42
6 6 1 5 E 45
```

## Adapting the example for your entry

Every entry-specific value in [`assemble.py`](assemble.py) is marked with
`# CHANGE FOR YOUR ENTRY:`:

1. **`CROSSLINK_DISTANCE`, `CROSSLINK_PSI`, `CROSSLINK_SIGMA` (lines 48–53):**
   distance threshold (Å) and any scoring parameters for your crosslinker.
2. **`ihm.System(title=...)` (line 119):** entry title, plus `system.authors`,
   `system.citations`, `system.software`, and per-entity
   `ihm.reference.UniProtSequence` alignments (see
   [Polishing](#polishing-for-a-real-deposition)).
3. **`CXMSDataset`, `EMDensityDataset`, starting-model datasets and the
   `DatasetGroup` that cites them (lines 183–197):** archive locations
   (`PRIDELocation`, `EMDBLocation`, `PDBLocation`, `ModelArchiveLocation`,
   `AlphaFoldDBLocation`) or DOI `InputFileLocation`s, plus `StartingModel`
   links (see [Polishing](#polishing-for-a-real-deposition)).
4. **`CrossLinkRestraint(..., linker=...)` (line 227):** crosslinker descriptor
   (prefer `ihm.cross_linkers.<name>` so SMILES and InChI are recorded; see
   [Polishing](#polishing-for-a-real-deposition)).
5. **`EM3DRestraint(..., fitting_method=...)` and `em_restraint.fits[model]`
   (lines 253, 296):** fitting method and per-model fit (remove both if your
   entry has no 3DEM map).
6. **`Protocol`, `Step`, `ClusterStep` (lines 266–275):** sampling method,
   model counts (`num_models_begin`, `num_models_end`), `multi_scale=True`, and
   post-processing steps.
7. **`Ensemble` (lines 305–308):** cluster model count, clustering method,
   clustering feature, and any `LocalizationDensity` maps (see
   [Polishing](#polishing-for-a-real-deposition)).
