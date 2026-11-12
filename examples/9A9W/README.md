# 9A9W — an atomic integrative structure

`assemble.py` rebuilds PDB-IHM entry [9A9W][entry], *USP7 bound to a
nucleosome/p53 complex*, out of what a modeling project actually ends up
with: a coordinate file, the crosslinks identified by crosslinking-MS, and
the subset of them that were used as restraints. gemmi reads the
coordinates; python-ihm describes everything else and writes the IHMCIF file
that PDB-IHM accepts.

Read it with the script open beside you. Each section below takes one topic,
explains it, shows the code that records it, and shows the mmCIF rows that
code produces. The last two sections cover what the script deliberately
leaves out and what to change for your own entry.

[entry]: https://pdb-ihm.org/entry/9A9W

## What it builds

- 9 entities in 19 chains, 51729 atoms, all-atom throughout.
- DSSO crosslinking-MS (PRIDE `PXD054141`) and a 3DEM map (`EMD-53517`),
  plus an AlphaFold DB model and PDB `9R04` as input models.
- 90 identified crosslinks and 51 chain-resolved restraints.
- `data/assembled.cif`: 32 mmCIF categories, about 4.5 MB.

Reproduced exactly: all eight polymer sequences, all 51729 atom positions
(same atoms, no coordinate differs), and both crosslink tables row for row.
Not written at all: author residue numbering, software, the citation,
UniProt reference sequences and starting models. Those are the subject of
[Polishing](#polishing-for-a-real-deposition) — a real deposition wants
them, and each is a few lines.

### Running it

```bash
pip install gemmi ihm          # neither is a dependency of ihmtools
cd examples/9A9W
python assemble.py
```

```
wrote /…/examples/9A9W/data/assembled.cif
9 entities, 2 restraints, 90 measurements, 51 restrained
```

That last line is the read-back check at the end of the script. In the
terminology used here, "measurements" are the identified crosslinks and
"restrained" counts the chain-resolved restraints.

`data/assembled.cif` is gitignored: the script writes it on every run and no
copy is committed. Tested with gemmi 0.7.5 and python-ihm 2.12.

## The input files

| file | what it is | read with |
|---|---|---|
| `coordinates.cif` | the final model, 51729 atoms, 19 chains | gemmi |
| `crosslinks.csv` | 90 identified crosslinks | `csv` |
| `restraints.csv` | 51 chain-resolved restraints | `csv` |
| `9A9W.png` | preview image for the deposition | not read |

`coordinates.cif` is an ordinary PDBx/mmCIF coordinate file. Two things in
it matter beyond the atoms: `_entity.pdbx_description`, which is
how `crosslinks.csv` names its proteins, and the entity grouping, from which
gemmi derives one sequence per entity and one subchain per copy.

### Residue numbering: entity `seq_id`, not author numbers

This is the mistake that produces a wrong file without any error. python-ihm
addresses residues by entity `seq_id` — the position in the entity's full
sequence, `_entity_poly_seq.num`, which equals `_atom_site.label_seq_id`.
Author numbering (`auth_seq_id`) is what papers usually quote, and a
crosslinking-MS search reports positions in the FASTA sequence it searched;
in 9A9W `seq_id` and `auth_seq_id` differ in every chain:

| chain | entity | sequence | `seq_id` modeled | `label − auth` |
|---|---|---|---|---|
| A, E | 1 Histone H3.1 | 139 aa | 42–137 | +3 |
| B, F | 2 Histone H4 | 106 aa | 26–106 | +3 |
| C | 3 Histone H2A | 133 aa | 21–122 | +3 |
| G | 3 Histone H2A | 133 aa | 15–122 | +3 |
| D, H | 4 Histone H2B | 128 aa | 35–128 | +3 |
| I | 5 DNA 153-mer | 153 nt | 11–134 | +3 |
| J | 6 DNA 153-mer | 153 nt | 20–143 | +3 |
| K, L, N | 7 p53 | 417 aa | 116–383 | +24 |
| M | 7 p53 | 417 aa | 116–389 | +24 |
| O | 8 USP7 | 1126 aa | 87–1126 | +24 |
| P, Q, R, S | 9 ZINC ION | 1 atom | — | — |

Most of the offsets are expression tags: `GSH` before each histone, and a
24-residue Strep-tag II / TEV sequence before p53 and USP7; the two DNA
strands are simply numbered from three positions into the entity sequence.
`Entity.residue(n)` takes
`n` as a `seq_id` and does not check it against anything, so author-numbered
input is offset without an error — by a different amount per chain. Map the
positions your search reports onto the entity sequence before you build the
links.

Two checks worth copying. All 180 endpoints in `crosslinks.csv` are lysines
when read as `seq_id`, which is what an amine-reactive reagent like DSSO
requires; read as author numbers, 10 of them fall off the end of the
sequence and the rest hit 18 different residue types. And the observed
ranges above are per *chain*, not per entity: chains C and G of the same
histone H2A differ, and so do the four p53 copies.

### `crosslinks.csv` — the identified crosslinks

One row per unique residue pair from the crosslinking-MS search, at entity
level, with no chain assignment.

| column | read | meaning |
|---|---|---|
| `id` | yes | key that `restraints.csv` refers to |
| `protein1`, `protein2` | yes | `_entity.pdbx_description` of the entities |
| `residue1`, `residue2` | yes | entity `seq_id` of the two endpoints |
| `linker` | **no** | the reagent is one per restraint, see below |

### `restraints.csv` — the chain-resolved restraints

The subset used in modeling, with the copies resolved. 51 rows over 50
distinct `crosslink_id`s.

| column | meaning |
|---|---|
| `crosslink_id` | `id` in `crosslinks.csv` |
| `chain1`, `chain2` | `_struct_asym.id`, i.e. `label_asym_id` |

## Block by block

### Reading the coordinates

gemmi supplies three things python-ihm needs and the CSVs cannot give: the
full sequence per entity, the grouping of chains into copies of an entity,
and the entity descriptions.

```python
structure = gemmi.read_structure(COORDINATES)
structure.setup_entities()
structure.assign_label_seq_id()

block = gemmi.cif.read(COORDINATES).sole_block()
category = block.get_mmcif_category("_entity")
descriptions = dict(zip(category["id"], category["pdbx_description"]))
```

`setup_entities()` and `assign_label_seq_id()` are what make
`residue.label_seq` and `residue.subchain` available below; without them the
script has no `seq_id` to write.

### The molecular system: entities and asym units

IHMCIF separates *what a molecule is* (an entity, one per unique sequence)
from *where a copy of it sits* (an asym unit, one per chain). Build both in
one pass:

```python
for gemmi_entity in structure.entities:
    description = descriptions[gemmi_entity.name]

    if gemmi_entity.entity_type == gemmi.EntityType.Polymer:
        alphabet = (ihm.DNAAlphabet
                    if gemmi_entity.polymer_type == gemmi.PolymerType.Dna
                    else ihm.LPeptideAlphabet)
        comps = alphabet()._comps
        sequence = [
            comps.get(name)
            or comps[gemmi.find_tabulated_residue(name).one_letter_code.upper()]
            for name in gemmi_entity.full_sequence]
        entity = ihm.Entity(sequence, alphabet=alphabet, description=description)
    else:
        name = next(residue.name for chain in structure[0] for residue in chain
                    if residue.subchain in gemmi_entity.subchains)
        entity = ihm.Entity([ihm.NonPolymerChemComp(name, name=description)],
                            description=description)

    entities[gemmi_entity.name] = entity
    for subchain in gemmi_entity.subchains:
        asyms[subchain] = ihm.AsymUnit(entity, details=description, id=subchain)
```

The two alphabets are keyed differently — `DNAAlphabet` by `DA`,
`LPeptideAlphabet` by the one-letter code — hence the name-first lookup. The
four zincs are non-polymers: each needs a single-component entity of its
own, and gets no residue range anywhere.

```
loop_
_entity.id
_entity.type
_entity.src_method
_entity.pdbx_description
_entity.formula_weight
_entity.pdbx_number_of_molecules
_entity.details
1 polymer man 'Histone H3.1' 15719.480 2 .
2 polymer man 'Histone H4' 11676.731 2 .
3 polymer man 'Histone H2A' 14447.871 2 .
4 polymer man 'Histone H2B' 14088.375 2 .
5 polymer man 'DNA 153-mer' 46982.000 1 .
6 polymer man 'DNA 153-mer' 47471.302 1 .
7 polymer man 'Cellular tumor antigen p53' 46495.302 4 .
8 polymer man 'Ubiquitin carboxyl-terminal hydrolase 7' 131257.357 1 .
9 non-polymer man 'ZINC ION' . 4 .
```

Note entities 5 and 6: both DNA strands carry the same description, so the
`by_description` map built next is **not** injective and one strand
overwrites the other in it. Nothing in 9A9W is affected, because no
crosslink touches the DNA, but in an adapted script a collision there
silently redirects crosslinks to the wrong entity.

```python
by_description = {e.description: e for e in entities.values()}
by_chain = {a.id: a for a in asyms.values()}
```

`system.asym_units` then produces one row per chain copy, with the
`id=subchain` given above used as the chain identifier:

```
loop_
_struct_asym.id
_struct_asym.entity_id
_struct_asym.details
A 1 'Histone H3.1'
E 1 'Histone H3.1'
B 2 'Histone H4'
F 2 'Histone H4'
C 3 'Histone H2A'
G 3 'Histone H2A'
D 4 'Histone H2B'
H 4 'Histone H2B'
# ... 19 rows in all
```

### The assembly

```python
system = ihm.System(title="USP7 bound to a nucleosome/p53 complex")
system.authors.extend(["Chakraborty, D.", "Kempf, G.", "Kater, L.",
                       "Cavadini, S.", "Thoma, N.H."])
system.entities.extend(entities.values())
system.asym_units.extend(asyms.values())

assembly = ihm.Assembly(list(asyms.values()), name="Modeled assembly")
```

The name passed here does not appear in the output. python-ihm always emits
a "complete assembly" holding everything in the system, and an assembly with
the same contents — as this one has, since it lists every asym unit — is
written as that one, name and all. The file ends up with a single assembly,
which every model and restraint then refers to:

```
loop_
_ihm_struct_assembly.id
_ihm_struct_assembly.name
_ihm_struct_assembly.description
1 'Complete assembly' 'All known components'
```

### Representation

The representation says how each part of the system was modeled. 9A9W is
atomic, so every asym unit gets one `AtomicSegment` — but over the residues
that are actually present, not the whole sequence: chain A holds 96 of its
139 residues.

```python
observed = {}
for chain in structure[0]:
    for residue in chain:
        if residue.label_seq is not None:
            observed.setdefault(residue.subchain, []).append(residue.label_seq)

# A residue range only means anything for a polymer; the zincs take the whole
# asym unit. (Ask the entity rather than checking for seq ids -- older gemmi
# assigns label_seq to non-polymers too.)
representation = ihm.representation.Representation([
    ihm.representation.AtomicSegment(
        asym(min(observed[sid]), max(observed[sid]))
        if asym.entity.is_polymeric() else asym,
        rigid=False)
    for sid, asym in asyms.items()])
```

Ask the entity whether it is polymeric rather than testing for `seq_id`s:
older gemmi assigns `label_seq` to non-polymers too, and python-ihm refuses
a residue range on a ligand.

```
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
1 1 1 'Histone H3.1' A 1 atomistic . flexible by-atom . .
2 1 1 'Histone H3.1' E 1 atomistic . flexible by-atom . .
3 1 2 'Histone H4' B 2 atomistic . flexible by-atom . .
4 1 2 'Histone H4' F 2 atomistic . flexible by-atom . .
5 1 3 'Histone H2A' C 3 atomistic . flexible by-atom . .
6 1 3 'Histone H2A' G 4 atomistic . flexible by-atom . .
7 1 4 'Histone H2B' D 5 atomistic . flexible by-atom . .
8 1 4 'Histone H2B' H 5 atomistic . flexible by-atom . .
# ... 19 rows in all
```

`model_granularity` is `by-atom` here, and the ranges land in a separate
category, one row per distinct range — which is why H2A appears twice, chain
C as 21–122 and chain G as 15–122:

```
loop_
_ihm_entity_poly_segment.id
_ihm_entity_poly_segment.entity_id
_ihm_entity_poly_segment.seq_id_begin
_ihm_entity_poly_segment.seq_id_end
_ihm_entity_poly_segment.comp_id_begin
_ihm_entity_poly_segment.comp_id_end
1 1 42 137 PRO GLU
2 2 26 106 LEU GLY
3 3 21 122 ARG LYS
4 3 15 122 ARG LYS
5 4 35 128 ARG ALA
6 5 11 134 DA DA
# ... 18 rows in all
```

### The input datasets

Each experiment or model that went into the structure becomes a dataset with
its archive accession. The class picks the `data_type`, and the location
class picks the database:

```python
crosslink_data = ihm.dataset.CXMSDataset(ihm.location.PRIDELocation("PXD054141"))
em_data = ihm.dataset.EMDensityDataset(ihm.location.EMDBLocation("EMD-53517"))
alphafold_data = ihm.dataset.DeNovoModelDataset(
    ihm.location.AlphaFoldDBLocation("AF-Q93009-F1-v4"))
pdb_data = ihm.dataset.PDBDataset(ihm.location.PDBLocation("9R04"))
```

```
loop_
_ihm_dataset_list.id
_ihm_dataset_list.data_type
_ihm_dataset_list.database_hosted
_ihm_dataset_list.details
1 'Crosslinking-MS data' YES .
2 '3DEM volume' YES .
3 'De Novo model' YES .
4 'Experimental model' YES .
```

The accessions go in a second, linked category rather than in the dataset
row itself:

```
loop_
_ihm_dataset_related_db_reference.id
_ihm_dataset_related_db_reference.dataset_list_id
_ihm_dataset_related_db_reference.db_name
_ihm_dataset_related_db_reference.accession_code
_ihm_dataset_related_db_reference.version
_ihm_dataset_related_db_reference.details
1 1 PRIDE PXD054141 . .
2 2 EMDB EMD-53517 . .
3 3 AlphaFoldDB AF-Q93009-F1-v4 . .
4 4 PDB 9R04 . .
```

Creating a dataset does not put it in the file: python-ihm writes the
datasets that something cites. The restraints below cite the crosslinking and
3DEM data, but nothing in this script uses the two input models, so a dataset
group collects all four and every [protocol step](#the-modeling-protocol)
cites it. That also records which data each step used, as the released entry
does with its own *Fitting data* group:

```python
# python-ihm writes the datasets that something in the system cites: a
# restraint, a starting model, or a dataset group. Here the modeling steps
# below cite this group, which is how the AlphaFold DB and PDB models -- used
# by nothing else in this script -- get into the file. Nothing has to go in
# system.orphan_datasets, which is meant for data that nothing cites.
fitting_data = ihm.dataset.DatasetGroup(
    [crosslink_data, em_data, alphafold_data, pdb_data],
    name="Fitting data", application="modeling")
```

```
loop_
_ihm_dataset_group.id
_ihm_dataset_group.name
_ihm_dataset_group.application
_ihm_dataset_group.details
1 'Fitting data' modeling .
```

```
loop_
_ihm_dataset_group_link.group_id
_ihm_dataset_group_link.dataset_list_id
1 1
1 2
1 3
1 4
```

### Crosslinks: two layers, two categories

This is the part of IHMCIF that most often goes in wrong, because the data
has two layers and they are not the same length.

**Identified crosslinks** (`_ihm_cross_link_list`) are the unique residue
pairs the search reported. They are defined on *entities*, so they carry no
chain assignment at all. 9A9W has 90, and they divide as:

| | count |
|---|---|
| inter-protein | 39 |
| intra-protein, in single-copy USP7 | 35 |
| intra-protein, in a multi-copy entity | 14 |
| homomeric self-links | 2 |

An intra-protein crosslink in an entity present in several copies — the
histones twice, p53 four times — is **ambiguous between intra- and
inter-molecular**: the identification cannot say whether the two peptides came
from the same copy. A homomeric self-link, the same residue on both
peptides, must be inter-molecular, but says nothing about which copies. The
two in 9A9W are p53 K381 (id 23) and H4 K95 (id 62).

**Chain-resolved restraints** (`_ihm_cross_link_restraint`) are what the
modeling actually applied, with copies resolved: one row per chain pair.
Several rows sharing a crosslink mean an **ambiguous restraint**, satisfied
by one or all of the alternatives. 9A9W has 51 rows over 50 crosslinks — 35
intra-molecular and 16 inter-molecular — and the one crosslink with two rows
is self-link 23, restrained on the alternative copy pairs L–M and N–K.

Of the other 40 identified crosslinks, none is restrained: 25 have an
endpoint outside the modeled residues, and none of the 14 copy-ambiguous
ones is used (13 of those also have an unmodeled endpoint). No restrained
crosslink has a missing endpoint.

The reagent is one per `CrossLinkRestraint`, not one per row — which is why
the `linker` column of the CSV is never read:

```python
crosslink_restraint = ihm.restraint.CrossLinkRestraint(
    dataset=crosslink_data, linker=ihm.ChemDescriptor("DSSO"))
```

```
loop_
_ihm_chemical_component_descriptor.id
_ihm_chemical_component_descriptor.auth_name
_ihm_chemical_component_descriptor.chemical_name
_ihm_chemical_component_descriptor.common_name
_ihm_chemical_component_descriptor.smiles
_ihm_chemical_component_descriptor.smiles_canonical
_ihm_chemical_component_descriptor.inchi
_ihm_chemical_component_descriptor.inchi_key
1 DSSO . . . . . .
```

`ihm.ChemDescriptor("DSSO")` records the name and nothing else; the
[polishing](#polishing-for-a-real-deposition) section fills in the
chemistry. Then the two layers, in order:

```python
experimental = {}                              # crosslink id -> link
for row in csv.DictReader(open(CROSSLINKS)):
    link = ihm.restraint.ExperimentalCrossLink(
        by_description[row["protein1"]].residue(int(row["residue1"])),
        by_description[row["protein2"]].residue(int(row["residue2"])))
    experimental[row["id"]] = link
    crosslink_restraint.experimental_cross_links.append([link])
```

Each identified crosslink is appended as a one-element list. A list with
several elements would mean one identification that could not be assigned to a
single residue pair — a different kind of ambiguity from the chain ambiguity
above, and not what 9A9W has.

The columns, with no `asym_id` among them — that is the point of this layer:

```
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
# ... 90 rows in all
```

Self-link 23, p53 K381 to K381, as written (python-ihm wraps a long row at
80 columns, so it occupies two lines):

```
23 23 'Cellular tumor antigen p53' 7 381 LYS 'Cellular tumor antigen p53' 7 381
LYS 1 DSSO 1 .
```

The restraints then name the chains, and the distance object is shared by
every one of them:

```python
# CHANGE FOR YOUR ENTRY: one bound shared by every link. restraints.csv
# has no per-link distance column -- add one if your links differ.
distance = ihm.restraint.UpperBoundDistanceRestraint(30.0)
for row in csv.DictReader(open(RESTRAINTS)):
    crosslink_restraint.cross_links.append(ihm.restraint.ResidueCrossLink(
        experimental_cross_link=experimental[row["crosslink_id"]],
        asym1=by_chain[row["chain1"]], asym2=by_chain[row["chain2"]],
        distance=distance))
```

```
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
# ... 51 rows in all
```

The two rows crosslink 23 produced:

```
9 23 7 L 381 LYS 7 M 381 LYS . . 'upper bound' . by-residue 30.000 . . . NO
38 23 7 N 381 LYS 7 K 381 LYS . . 'upper bound' . by-residue 30.000 . . . NO
```

Both rows carry `group_id` 23, which is what makes them alternatives for the
same crosslink rather than two independent restraints. `model_granularity`
is `by-residue`, following the atomic representation —
`ResidueCrossLink` restrains a named residue, so a coarse-grained entry with
beads spanning several residues needs `FeatureCrossLink` instead.
`conditional_crosslink_flag` is empty: pass `restrain_all=True` or `False`
to `ResidueCrossLink` to say that ALL or ANY of an ambiguous restraint's
alternatives must be satisfied. 9A9W does not set it.

### The 3DEM restraint

```python
em_restraint = ihm.restraint.EM3DRestraint(
    dataset=em_data, assembly=assembly, fitting_method="Flexible fitting")
system.restraints.extend([crosslink_restraint, em_restraint])
```

```
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
1 2 'Flexible fitting' . 1 . . 1 . .
```

The `model_id` column is the catch. `_ihm_3dem_restraint` is written once
per *fit of a model*, so a restraint with no fit recorded is not written at
all. The line that records it comes later, after the model exists, and it is
easy to miss:

```python
# Required: _ihm_3dem_restraint is written only for models in
# em_restraint.fits. Without this line the 3DEM restraint is omitted from the
# file, without a warning.
em_restraint.fits[model] = ihm.restraint.EM3DRestraintFit()
```

Delete that one line and `_ihm_3dem_restraint` disappears from the output
and the read-back count drops from 2 restraints to 1, with no warning — the
restraint is still in `system.restraints`, it just has nothing to attach to.

### The modeling protocol

One step per thing you did, in order, each naming the assembly it acted on.

```python
# CHANGE FOR YOUR ENTRY: the modeling steps and methods actually used.
protocol = ihm.protocol.Protocol(name="Modeling")
for name, method in [("Structure prediction", "AlphaFold2"),
                     ("Rigid body fitting", "Rigid body fitting"),
                     ("Model editing", "Manual editing"),
                     ("Flexible fitting", "Molecular dynamics flexible fitting"),
                     ("Refinement", "Maximum-likelihood refinement")]:
    protocol.steps.append(ihm.protocol.Step(
        assembly=assembly, dataset_group=fitting_data, method=method,
        name=name, num_models_begin=None, num_models_end=None))
# The model below cites this protocol, and that is what writes it.
```

```
loop_
_ihm_modeling_protocol.id
_ihm_modeling_protocol.protocol_name
_ihm_modeling_protocol.num_steps
_ihm_modeling_protocol.details
1 Modeling 5 .
```

```
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
1 1 1 1 1 'Structure prediction' AlphaFold2 . . NO NO NO NO . . .
2 1 2 1 1 'Rigid body fitting' 'Rigid body fitting' . . NO NO NO NO . . .
3 1 3 1 1 'Model editing' 'Manual editing' . . NO NO NO NO . . .
4 1 4 1 1 'Flexible fitting' 'Molecular dynamics flexible fitting' . . NO NO NO
NO . . .
5 1 5 1 1 Refinement 'Maximum-likelihood refinement' . . NO NO NO NO . . .
```

`dataset_group_id` is the *Fitting data* group from the datasets section.
`software_id` is empty because this script never builds an `ihm.Software`;
[polishing](#polishing-for-a-real-deposition) adds one per step. `multi_scale_flag` is `NO`, correctly for an all-atom
representation — a coarse-grained or mixed entry has to pass
`multi_scale=True`.

### The model, and streaming its atoms

`ihm.model.Model` is an interface: override `get_atoms()` and python-ihm
pulls the coordinates out as it writes, rather than holding a second copy of
51729 atoms in memory.

```python
class Model(ihm.model.Model):
    """Streams atoms from gemmi rather than holding a second copy of them."""

    def get_atoms(self):
        for chain in structure[0]:
            for residue in chain:
                # Non-polymers have no label_seq, but a single-component
                # entity's seq_id is always 1.
                seq_id = residue.label_seq or 1
                for atom in residue:
                    yield ihm.model.Atom(
                        asym_unit=asyms[residue.subchain], seq_id=seq_id,
                        atom_id=atom.name, type_symbol=atom.element.name,
                        x=atom.pos.x, y=atom.pos.y, z=atom.pos.z,
                        het=residue.het_flag == "H",
                        biso=atom.b_iso, occupancy=atom.occ)
```

Non-polymers have no `label_seq`, so `seq_id` falls back to 1, which is
correct for a single-component entity. Skipping those atoms instead is the
other common failure: the write then dies with *"Assemblies reference asym
IDs that don't have coordinates"*.

```python
model = Model(assembly=assembly, protocol=protocol,
              representation=representation, name="Best scoring model")
```

```python
system.state_groups.append(ihm.model.StateGroup(
    [ihm.model.State([ihm.model.ModelGroup([model], name="All models")])]))
```

The nesting — state group, state, model group, model — is IHMCIF's way of
holding several conformations or compositions in one entry. A single
deterministic model still needs all four levels:

```
loop_
_ihm_model_list.model_id
_ihm_model_list.model_name
_ihm_model_list.assembly_id
_ihm_model_list.protocol_id
_ihm_model_list.representation_id
1 'Best scoring model' 1 1 1
```

```
loop_
_ihm_model_group.id
_ihm_model_group.name
_ihm_model_group.details
1 'All models' .
```

```
loop_
_ihm_model_group_link.group_id
_ihm_model_group_link.model_id
1 1
```

The atoms themselves are ordinary `_atom_site` rows with one IHMCIF column
added, `ihm_model_id`:

```
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_seq_id
_atom_site.auth_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.label_asym_id
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.label_entity_id
_atom_site.auth_asym_id
_atom_site.auth_comp_id
_atom_site.B_iso_or_equiv
_atom_site.pdbx_PDB_model_num
_atom_site.ihm_model_id
ATOM 1 N N . PRO 42 42 ? A 170.268 122.820 145.131 1.000 1 A PRO 374.930 1 1
ATOM 2 C CA . PRO 42 42 ? A 170.646 123.665 146.267 1.000 1 A PRO 374.930 1 1
# ... 51729 rows in all
```

Note `label_seq_id` and `auth_seq_id` are both 42 in that row, where the
input coordinates have 42 and 39. See
[polishing](#polishing-for-a-real-deposition).

### Writing, and reading back

```python
with open(OUTPUT, "w") as fh:
    ihm.dumper.write(fh, [system])
print("wrote %s" % OUTPUT)

# Read it back -- the cheapest check that what we wrote is well formed.
with open(OUTPUT) as fh:
    check, = ihm.reader.read(fh)
crosslinks = check.restraints[0]
print("%d entities, %d restraints, %d measurements, %d restrained"
      % (len(check.entities), len(check.restraints),
         sum(len(g) for g in crosslinks.experimental_cross_links),
         len(crosslinks.cross_links)))
```

The read-back is the cheapest check there is: `ihm.reader.read` parses what
was just written and the counts come back from the file rather than from the
objects in memory. It catches a dropped restraint — such as the 3DEM one
above — that no exception would report.

## Checking the file

Three checks, cheapest first.

**1. Dictionary compliance**, locally, in a second:

```python
import ihm.dictionary

with open("mmcif_pdbx_v50.dic") as fh:
    d = ihm.dictionary.read(fh)
with open("mmcif_ihm_ext.dic") as fh:
    d += ihm.dictionary.read(fh)
with open("data/assembled.cif") as fh:
    d.validate(fh)                      # raises ValidatorError on a problem
```

**Fetch both dictionaries fresh**, from
[wwpdb-dictionaries/mmcif_pdbx][pdbx] and [ihmwg/IHMCIF][ihm] (the IHM one
is also distributed as `mmcif_ihm.dic`; same file). A stale copy is the most
likely cause of a failure here, and it looks exactly like a real one.
python-ihm 2.12 writes `_pdbx_poly_seq_scheme.ihm_model_id_list`, which
IHM extension 1.29 added; that version reached the distributed
`mmcif_ihm_ext.dic` only recently, so a copy fetched on 2026-10-02 still
stops at 1.28 and rejects correct output with *"The following keywords are
not defined in the dictionary"*.

**2. The validation server**, for the report a curator will read:

```bash
ihmv --mode dev run data/assembled.cif
```

**3. Deposit it:**

```bash
ihmdep upload data/assembled.cif --image data/9A9W.png
```

See the [main README](../../README.md) for the full deposition workflow —
login, status polling, `set_status`, downloading generated files.

[pdbx]: https://github.com/wwpdb-dictionaries/mmcif_pdbx
[ihm]: https://github.com/ihmwg/IHMCIF

## Polishing for a real deposition

The script stops at a file that is structurally complete and dictionary
valid. A deposition wants more, and each piece is a few lines. Everything
below was run by appending it to a copy of the script, just before
`ihm.dumper.write`; the output rows are from that run.

### The crosslinking reagent's chemistry

`ihm.cross_linkers` carries the standard reagents with their SMILES and
InChI, so the entry says what was actually used:

```python
# --- the crosslinking reagent, with its chemistry -------------------------
import ihm.cross_linkers

crosslink_restraint.linker = ihm.cross_linkers.dsso
```

```
loop_
_ihm_chemical_component_descriptor.id
_ihm_chemical_component_descriptor.auth_name
_ihm_chemical_component_descriptor.chemical_name
_ihm_chemical_component_descriptor.common_name
_ihm_chemical_component_descriptor.smiles
_ihm_chemical_component_descriptor.smiles_canonical
_ihm_chemical_component_descriptor.inchi
_ihm_chemical_component_descriptor.inchi_key
1 DSSO 'disuccinimidyl sulfoxide' .
O=C(CCS(CCC(ON1C(CCC1=O)=O)=O)=O)ON2C(CCC2=O)=O .
1S/C14H16N2O9S/c17-9-1-2-10(18)15(9)24-13(21)5-7-26(23)8-6-14(22)25-16-11(19)3-4-12(16)20/h1-8H2
XJSVVHDQSGMHAJ-UHFFFAOYSA-N
```

That is identical to the released entry's own descriptor. The module also
ships `sda`, `bs3`, `dsbu`, `dsg`, `dss`, `edc`, `phoX`, `bmso`, `dhso`,
`tbuphoX` and `photo_leucine`.

### Author residue numbering

`AsymUnit` defaults to `auth_seq_id_map=0`, which makes `auth_seq_id` equal
`seq_id` — so the file above renumbers every residue and loses the numbering
the paper uses. Take the offset from the coordinates:

```python
# --- author residue numbering --------------------------------------------
# AsymUnit(auth_seq_id_map=0) makes auth_seq_id equal seq_id. Take the
# offset from the coordinates so the file keeps the entry's own numbering.
for chain in structure[0]:
    for residue in chain:
        if residue.label_seq is not None:
            asyms[residue.subchain].auth_seq_id_map = (
                residue.seqid.num - residue.label_seq)
```

With that, all 2978 polymer positions in `_atom_site` match the released
entry's `auth_seq_id`; without it, none of them do.

### Software

One `ihm.Software` per protocol step fills `_software` and the step's
`software_id`:

```python
# --- software, one program per protocol step ------------------------------
SOFTWARE = [
    ("AlphaFold Multimer", "2.3", "structure prediction",
     "De novo structure prediction",
     "https://cosmic-cryoem.org/tools/alphafoldmultimer/"),
    ("ChimeraX", "1.8", "model building", "Rigid body fitting",
     "https://www.rbvi.ucsf.edu/chimerax/"),
    ("Coot", "0.9.8.1", "model building", "Model editing",
     "https://www2.mrc-lmb.cam.ac.uk/personal/pemsley/coot/"),
    ("Isolde", "1.7", "model building", "MDFF with 3DEM map potentials",
     "https://tristanic.github.io/isolde/"),
    ("Phenix", "1.20.1", "refinement", "Model refinement against 3DEM map",
     "https://phenix-online.org/"),
]
for step, (name, version, classification, details, url) in zip(
        protocol.steps, SOFTWARE):
    step.software = ihm.Software(name=name, classification=classification,
                                 description=details, location=url,
                                 version=version)
    system.software.append(step.software)
```

```
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
1 1 1 1 1 'Structure prediction' AlphaFold2 . . NO NO NO NO 1 . .
2 1 2 1 1 'Rigid body fitting' 'Rigid body fitting' . . NO NO NO NO 2 . .
3 1 3 1 1 'Model editing' 'Manual editing' . . NO NO NO NO 3 . .
4 1 4 1 1 'Flexible fitting' 'Molecular dynamics flexible fitting' . . NO NO NO
NO 4 . .
5 1 5 1 1 Refinement 'Maximum-likelihood refinement' . . NO NO NO NO 5 . .
```

### The citation

```python
# --- the primary citation ------------------------------------------------
system.citations.append(ihm.Citation(
    pmid=None, title="Nucleosomes specify co-factor access to p53",
    journal="Mol.Cell", volume=None, page_range=None, year=2025,
    doi="10.1016/j.molcel.2025.06.027", is_primary=True,
    authors=["Chakraborty, D.", "Sandate, C.R.", "Isbel, L.", "Kempf, G.",
             "Weiss, J.", "Cavadini, S.", "Kater, L.", "Seebacher, J.",
             "Kozicka, Z.", "Stoos, L.", "Grand, R.S.", "Schubeler, D.",
             "Michael, A.K.", "Thoma, N.H."]))
```

`is_primary=True` is what makes the `id` column read `primary`:

```
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
primary 'Nucleosomes specify co-factor access to p53' Mol.Cell . . . 2025 . . .
. 10.1016/j.molcel.2025.06.027
```

### Reference sequences

A reference sequence ties each entity to its database entry and records how
the modeled sequence aligns to it. `from_accession` fetches the sequence
from UniProt:

```python
# --- reference sequences, fetched from UniProt ---------------------------
import ihm.reference

# entity description -> accession, last UniProt position the entity covers,
# and the entity seq_id its first residue aligns to.
REFERENCES = {
    "Histone H3.1": ("P68431", 136, 4),
    "Histone H4": ("P62805", 103, 4),
    "Histone H2A": ("P04908", 130, 4),
    "Histone H2B": ("P06899", 125, 4),
    "Cellular tumor antigen p53": ("P04637", 393, 25),
    "Ubiquitin carboxyl-terminal hydrolase 7": ("Q93009", 1102, 25),
}
for description, (accession, db_end, entity_begin) in REFERENCES.items():
    reference = ihm.reference.UniProtSequence.from_accession(accession)
    reference.alignments.append(ihm.reference.Alignment(
        db_begin=1, db_end=db_end, entity_begin=entity_begin,
        entity_end=entity_begin + db_end - 1))
    by_description[description].references.append(reference)
```

```
loop_
_struct_ref_seq.align_id
_struct_ref_seq.ref_id
_struct_ref_seq.seq_align_beg
_struct_ref_seq.seq_align_end
_struct_ref_seq.db_align_beg
_struct_ref_seq.db_align_end
1 1 4 139 1 136
2 2 4 106 1 103
3 3 4 133 1 130
4 4 4 128 1 125
5 5 25 417 1 393
6 6 25 1126 1 1102
```

`db_end` is the last database position the entity covers, which is not
always the length of the database sequence: H2B stops one residue short of
P06899, hence 125 rather than 126.

python-ihm **checks the alignment** against the entity sequence and raises
rather than writing a wrong one, naming the three things you can adjust
(message wrapped here; it also prints both sequences and a match line):

```
ValueError: Reference sequence from <ihm.reference.UniProtSequence('P04637')>
does not match entity canonical sequence (after mutations) for
<ihm.Entity(Cellular tumor antigen p53)> - you may need to adjust
Alignment.db_begin,db_end (1-393), Alignment.entity_begin,entity_end
(1-393), or add to Alignment.seq_dif
```

That is worth knowing, because the released 9A9W says `1-393 ↔ 1-393` for
p53 while its own entity sequence is 417 residues — the UniProt sequence
starts at `seq_id` 25, after the tag. For a real mismatch rather than an
offset, `ihm.reference.SeqDif` records the difference per residue.

### Starting models

Input models are already recorded as datasets above, but that does not say
which chain came from which model, nor at what offset. `StartingModel` does:

```python
# --- starting models ------------------------------------------------------
import ihm.startmodel

# chain, dataset it came from, chain id in that file, seq_id offset.
for chain_id, dataset, source_chain, offset in [
        ("O", alphafold_data, "F", 24),
        ("A", pdb_data, "A", 0),
        ("K", pdb_data, "K", 24)]:
    system.orphan_starting_models.append(ihm.startmodel.StartingModel(
        asym_unit=by_chain[chain_id], dataset=dataset, asym_id=source_chain,
        offset=offset))
```

```
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
1 8 'Ubiquitin carboxyl-terminal hydrolase 7' O 1 'ab initio model' F 24 3 .
2 1 'Histone H3.1' A 2 'experimental model' A 0 4 .
3 7 'Cellular tumor antigen p53' K 3 'experimental model' K 24 4 .
```

`offset` is added to the starting model's own numbering to give the entity
`seq_id`: the AlphaFold model of USP7 numbers its chain F 1–1102, and
1 + 24 = 25 is where that sequence starts in the entity. The values above
are the released entry's own. `starting_model_source` is not passed at all
— python-ihm derives it from the dataset class, so `DeNovoModelDataset`
gives *ab initio model* and `PDBDataset` gives *experimental model*.

The released entry has 24 such rows, one per chain per source — including
two for each p53 copy, from both an AlphaFold model and PDB 9R04. Three are
enough to show the shape.

### What the polished file gains

Six categories, on top of the 32 the script writes: `_software`,
`_citation`, `_citation_author`, `_struct_ref`, `_struct_ref_seq` and
`_ihm_starting_model_details`. It still passes both dictionaries.

## Adapting the example

The script marks the entry-specific points with `# CHANGE FOR YOUR ENTRY:`.
In script order:

| line | what to change |
|---|---|
| 90 | title and author list |
| 125 | archive accessions of the data used, and the group the steps cite |
| 164 | the crosslinking reagent |
| 187 | the distance bound — one value shared by all 51 links here |
| 196 | the 3DEM fitting method |
| 205 | the modeling steps and their methods |

Beyond those, three things depend on the shape of *your* data rather than on
a value:

- **Descriptions must be unique per entity** if you key crosslinks by them,
  as this script does. 9A9W gets away with a collision; your entry may not.
- **Residue numbers in your input must be entity `seq_id`s.** Convert them
  first, and check the endpoints land on residues the reagent can react
  with.
- **A coarse-grained or mixed representation changes three things at once:**
  the representation segments, the restraint granularity
  (`FeatureCrossLink` rather than `ResidueCrossLink` for a bead spanning
  several residues), and `multi_scale=True` on the protocol steps. See
  [`../9A8W`](../9A8W) for that case.
