#!/usr/bin/env python3
"""Rebuild PDB-IHM entry 9A9W from raw coordinates and a crosslink list.

9A9W is "USP7 bound to a nucleosome/p53 complex": histones, two DNA strands,
p53, USP7 and four zincs, with DSSO crosslinking MS and a 3DEM map. It reads
data/coordinates.cif, data/crosslinks.csv and data/restraints.csv, and writes
data/assembled.cif.

gemmi reads the coordinates; python-ihm describes everything else. This is a
worked example for one entry, not a general converter -- the metadata below is
9A9W's, and the code assumes what 9A9W contains.
"""

import csv
import os

import gemmi

import ihm
import ihm.dataset
import ihm.dumper
import ihm.location
import ihm.model
import ihm.protocol
import ihm.reader
import ihm.representation
import ihm.restraint

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
COORDINATES = os.path.join(DATA, "coordinates.cif")
CROSSLINKS = os.path.join(DATA, "crosslinks.csv")
RESTRAINTS = os.path.join(DATA, "restraints.csv")
OUTPUT = os.path.join(DATA, "assembled.cif")

# --------------------------------------------------------------------------
# Read the coordinates. gemmi gives the sequences, the chain grouping and the
# entity descriptions; those descriptions are what the crosslink list names
# its proteins by.
# --------------------------------------------------------------------------

structure = gemmi.read_structure(COORDINATES)
structure.setup_entities()
structure.assign_label_seq_id()

block = gemmi.cif.read(COORDINATES).sole_block()
category = block.get_mmcif_category("_entity")
descriptions = dict(zip(category["id"], category["pdbx_description"]))

# --------------------------------------------------------------------------
# One Entity per unique sequence, one AsymUnit per chain copy.
#
# The alphabets are keyed differently -- DNAAlphabet by "DA", LPeptideAlphabet
# by the one-letter code -- so look each component up by name first. The four
# zincs are non-polymers; each needs its own single-component entity.
# --------------------------------------------------------------------------

entities = {}                                  # gemmi entity name -> Entity
asyms = {}                                     # subchain id -> AsymUnit

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

# crosslinks.csv identifies proteins by _entity.pdbx_description, hence this
# map. It is not injective: both DNA strands are described "DNA 153-mer", so
# one overwrites the other. No effect here (DSSO is amine-reactive and no
# crosslink involves the DNA), but descriptions must be unique per entity.
by_description = {e.description: e for e in entities.values()}
by_chain = {a.id: a for a in asyms.values()}

# CHANGE FOR YOUR ENTRY: title and authors.
system = ihm.System(title="USP7 bound to a nucleosome/p53 complex")
system.authors.extend(["Chakraborty, D.", "Kempf, G.", "Kater, L.",
                       "Cavadini, S.", "Thoma, N.H."])
system.entities.extend(entities.values())
system.asym_units.extend(asyms.values())

assembly = ihm.Assembly(list(asyms.values()), name="Modeled assembly")

# --------------------------------------------------------------------------
# Representation: atomic, over the residues actually present. Chains have gaps
# -- chain A holds 96 of its 139 residues -- so describe what was modeled
# rather than the whole sequence.
# --------------------------------------------------------------------------

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

# --------------------------------------------------------------------------
# The data 9A9W was built from.
# --------------------------------------------------------------------------

# CHANGE FOR YOUR ENTRY: archive accession codes of the data used.
crosslink_data = ihm.dataset.CXMSDataset(ihm.location.PRIDELocation("PXD054141"))
em_data = ihm.dataset.EMDensityDataset(ihm.location.EMDBLocation("EMD-53517"))
alphafold_data = ihm.dataset.DeNovoModelDataset(
    ihm.location.AlphaFoldDBLocation("AF-Q93009-F1-v4"))
pdb_data = ihm.dataset.PDBDataset(ihm.location.PDBLocation("9R04"))

# python-ihm writes the datasets that something in the system cites: a
# restraint, a starting model, or a dataset group. Here the modeling steps
# below cite this group, which is how the AlphaFold DB and PDB models -- used
# by nothing else in this script -- get into the file. Nothing has to go in
# system.orphan_datasets, which is meant for data that nothing cites.
fitting_data = ihm.dataset.DatasetGroup(
    [crosslink_data, em_data, alphafold_data, pdb_data],
    name="Fitting data", application="modeling")

# --------------------------------------------------------------------------
# Crosslinks, in the two layers that IHMCIF uses.
#
# crosslinks.csv -> _ihm_cross_link_list: the identified crosslinks (unique
# residue pairs from the crosslinking-MS search), defined on entities, without
# chain assignment. In an entity present in several copies (histones x2,
# p53 x4), an intra-protein crosslink is ambiguous between intra- and
# inter-molecular, and a homomeric self-link (same residue on both peptides)
# is inter-molecular but ambiguous as to which copies. 9A9W has 90: 39
# inter-protein, 35 intra-protein in single-copy USP7, 14 intra-protein in
# multi-copy entities, and 2 self-links (p53 K381, H4 K95).
#
# restraints.csv -> _ihm_cross_link_restraint: the chain-resolved restraints
# used in modeling, keyed by crosslink id, one row per chain pair. Several
# rows for one id form an ambiguous restraint. 9A9W has 51 rows (35 intra-,
# 16 inter-molecular) from 50 crosslinks: self-link 23 is restrained on the
# alternative copy pairs L-M and N-K. ResidueCrossLink(restrain_all=...)
# records whether ANY or ALL alternatives must be satisfied
# (conditional_crosslink_flag); it is not set here. The other 40 identified
# crosslinks are not restrained; 25 of them have an endpoint outside the
# modeled residues.
# --------------------------------------------------------------------------

# CHANGE FOR YOUR ENTRY: crosslinking reagent.
# One reagent per CrossLinkRestraint; the linker column of crosslinks.csv is
# not read. ChemDescriptor("DSSO") records only the name in
# _ihm_chemical_component_descriptor; ihm.cross_linkers.dsso also supplies
# SMILES and InChI (likewise .sda, .bs3, .dsbu, ...).
crosslink_restraint = ihm.restraint.CrossLinkRestraint(
    dataset=crosslink_data, linker=ihm.ChemDescriptor("DSSO"))

# residue1/residue2 are entity seq_ids (_entity_poly_seq.num, equal to
# _atom_site.label_seq_id), not author residue numbers. In 9A9W
# label_seq_id - auth_seq_id is +3 for the histones and DNA and +24 for p53
# and USP7, so author-numbered input is shifted by a per-entity offset that
# python-ihm does not detect. Map positions reported by the crosslink search
# (database sequence numbering) onto the entity sequence; as a check, all 180
# endpoints here fall on lysines, as expected for DSSO.
experimental = {}                              # crosslink id -> link
for row in csv.DictReader(open(CROSSLINKS)):
    link = ihm.restraint.ExperimentalCrossLink(
        by_description[row["protein1"]].residue(int(row["residue1"])),
        by_description[row["protein2"]].residue(int(row["residue2"])))
    experimental[row["id"]] = link
    crosslink_restraint.experimental_cross_links.append([link])

# CHANGE FOR YOUR ENTRY: one bound shared by every link. restraints.csv
# has no per-link distance column -- add one if your links differ.
distance = ihm.restraint.UpperBoundDistanceRestraint(30.0)
for row in csv.DictReader(open(RESTRAINTS)):
    crosslink_restraint.cross_links.append(ihm.restraint.ResidueCrossLink(
        experimental_cross_link=experimental[row["crosslink_id"]],
        asym1=by_chain[row["chain1"]], asym2=by_chain[row["chain2"]],
        distance=distance))

# CHANGE FOR YOUR ENTRY: 3DEM fitting method.
em_restraint = ihm.restraint.EM3DRestraint(
    dataset=em_data, assembly=assembly, fitting_method="Flexible fitting")
system.restraints.extend([crosslink_restraint, em_restraint])

# --------------------------------------------------------------------------
# How the model was built.
# --------------------------------------------------------------------------

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


model = Model(assembly=assembly, protocol=protocol,
              representation=representation, name="Best scoring model")
# Required: _ihm_3dem_restraint is written only for models in
# em_restraint.fits. Without this line the 3DEM restraint is omitted from the
# file, without a warning.
em_restraint.fits[model] = ihm.restraint.EM3DRestraintFit()

system.state_groups.append(ihm.model.StateGroup(
    [ihm.model.State([ihm.model.ModelGroup([model], name="All models")])]))

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

# Not written by this script but expected in a deposition: _software
# (ihm.Software -> system.software), _citation (ihm.Citation ->
# system.citations), reference sequences (_struct_ref, via
# ihm.reference.UniProtSequence on each Entity) and starting models
# (_ihm_starting_model_details, via ihm.startmodel.StartingModel). The
# AlphaFold DB and PDB entries above are recorded only as input datasets,
# without the per-chain starting-model mapping.
