#!/usr/bin/env python3
"""Rebuild PDB-IHM entry 9A8W from a coarse-grained model and a crosslink list.

9A8W is "Integrative structure of human SNAPc-DNA": five SNAPc subunits and a
DNA duplex, from IMP replica-exchange sampling, restrained by SDA crosslinking
MS and a 3DEM map. It reads data/model.cif, data/crosslinks.csv and
data/restraints.csv, and writes data/assembled.cif.

Compare 9A9W/assemble.py, which does the same job for an atomic structure.
Everything that differs here follows from the model being coarse-grained:

  * there are no atoms at all -- 1663 spheres, so gemmi cannot read it as a
    structure and the model is taken from _ihm_sphere_obj_site directly
  * the representation is multi-scale: rigid by-residue segments for the
    regions built from known structures, flexible multi-residue beads elsewhere
  * a multi-residue bead has no single-residue coordinate, so restraints use
    granularity "by-residue" only when both endpoints are 1-residue spheres and
    "by-feature" whenever either endpoint is a multi-residue bead

This is a worked example for one entry, not a general converter. Values tied to
9A8W (title, linker, distance bound, archive accessions, protocol and ensemble
numbers) are marked "CHANGE FOR YOUR ENTRY" below.
"""

import csv
import os
from collections import defaultdict

import gemmi

import ihm
import ihm.analysis
import ihm.dataset
import ihm.dumper
import ihm.location
import ihm.model
import ihm.protocol
import ihm.reader
import ihm.representation
import ihm.restraint

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MODEL = os.path.join(DATA, "model.cif")
CROSSLINKS = os.path.join(DATA, "crosslinks.csv")
RESTRAINTS = os.path.join(DATA, "restraints.csv")
OUTPUT = os.path.join(DATA, "assembled.cif")

# CHANGE FOR YOUR ENTRY: SDA upper-bound distance in Angstroms
# (_ihm_cross_link_restraint.distance_threshold) and IMP's Bayesian nuisance
# parameters (_ihm_cross_link_restraint.psi, .sigma_1, .sigma_2).
CROSSLINK_DISTANCE = 22.0
CROSSLINK_PSI = 0.25
CROSSLINK_SIGMA = 2.0

# --------------------------------------------------------------------------
# Read data/model.cif. gemmi.read_structure() is no use here -- _atom_site is
# empty -- so read the mmCIF categories directly:
#   _entity          (id, pdbx_description -- must match crosslinks.csv names)
#   _entity_poly     (entity_id, type -- polypeptide(L) vs deoxyribonucleotide)
#   _entity_poly_seq (entity_id, mon_id, num -- 1-based entity seq_id)
#   _struct_asym     (id, entity_id -- chain IDs A..G used in restraints.csv)
#   _ihm_sphere_obj_site (asym_id, seq_id_begin..end, Cartn_x/y/z,
#                         object_radius)
# --------------------------------------------------------------------------

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

# --------------------------------------------------------------------------
# One Entity per sequence, one AsymUnit per chain. Every entity here is
# polymeric -- protein or DNA -- so there is no ligand case to handle (see
# 9A9W/assemble.py for non-polymer ligands).
#
# Writes: _entity, _entity_poly, _entity_poly_seq, _chem_comp, _struct_asym,
# _pdbx_poly_seq_scheme, _entry, _struct, _ihm_struct_assembly, and
# _ihm_struct_assembly_details.
# --------------------------------------------------------------------------

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

# --------------------------------------------------------------------------
# The spheres, and the representation they imply.
#
# Each row of _ihm_sphere_obj_site gives asym_id (chain A..G), 1-based
# inclusive entity seq_id_begin..seq_id_end, Cartn_x/y/z (Angstroms) and
# object_radius (Angstroms). Unmodeled regions (here subunit 4 / chain E
# residues 1..40, an N-terminal tag) have no spheres.
#
# A one-residue sphere (begin == end) is a residue-level bead from a known
# structure, sampled as a rigid body (ResidueSegment). A multi-residue sphere
# (begin < end) is a flexible coarse bead for a region without structure
# (FeatureSegment, count=1 sphere per segment).
#
# Writes: _ihm_entity_poly_segment, _ihm_model_representation, and
# _ihm_model_representation_details. (To also populate
# _ihm_starting_model_details, pass starting_model=ihm.startmodel.StartingModel
# to each template-derived ResidueSegment.)
# --------------------------------------------------------------------------

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
            current = (current[0], end, size)
            continue
        if current:
            segments.append((asym, current[0], current[1], current[2]))
        current = (begin, end, size)
    if current:
        segments.append((asym, current[0], current[1], current[2]))

representation = ihm.representation.Representation([
    ihm.representation.ResidueSegment(asym(begin, end), rigid=True,
                                      primitive="sphere")
    if size == 1 else
    ihm.representation.FeatureSegment(asym(begin, end), rigid=False,
                                      primitive="sphere", count=1)
    for asym, begin, end, size in segments])

# --------------------------------------------------------------------------
# CHANGE FOR YOUR ENTRY: experimental datasets and starting-model archives.
#
# Archive Location classes (PRIDELocation, EMDBLocation, ModelArchiveLocation,
# PDBLocation) set _ihm_dataset_list.database_hosted = YES and write
# _ihm_dataset_related_db_reference. The crosslinking MS is deposited in PRIDE
# so validation can check restraints against primary data.
# --------------------------------------------------------------------------

crosslink_data = ihm.dataset.CXMSDataset(ihm.location.PRIDELocation("PXD053341"))
em_data = ihm.dataset.EMDensityDataset(ihm.location.EMDBLocation("EMD-50730"))
alphafold_data = ihm.dataset.DeNovoModelDataset(
    ihm.location.ModelArchiveLocation("ma-pgtjz"))
pdb_9fso = ihm.dataset.PDBDataset(ihm.location.PDBLocation("9FSO"))
pdb_7zx8 = ihm.dataset.PDBDataset(ihm.location.PDBLocation("7ZX8"))
pdb_7xur = ihm.dataset.PDBDataset(ihm.location.PDBLocation("7XUR"))

# python-ihm writes the datasets that something in the system cites: a
# restraint, a starting model, or a dataset group. Here the sampling step
# below cites this group, which is how the template models -- used by nothing
# else in this script -- get into the file. Nothing has to go in
# system.orphan_datasets, which is meant for data that nothing cites.
input_data = ihm.dataset.DatasetGroup(
    [crosslink_data, em_data, alphafold_data, pdb_9fso, pdb_7zx8, pdb_7xur])

# --------------------------------------------------------------------------
# Crosslinks and the 3DEM restraint.
#
# data/crosslinks.csv -> _ihm_cross_link_list: 268 identified crosslinks
# (unique residue pairs; id,protein1,residue1,protein2,residue2,linker): 186
# intra-protein, 81 inter-protein, 1 homomeric self-link. All entities are
# single-copy, so there is no intra-/inter-molecular ambiguity and each pair
# maps to one chain pair.
#   * protein1/protein2 = _entity.pdbx_description; residue1/residue2 =
#     entity seq_id (_entity_poly_seq.num, counting subunit 4's 40-residue
#     N-terminal tag), not author numbering.
#   * The linker column is not read; the reagent is set once per
#     CrossLinkRestraint (SDA -> _ihm_chemical_component_descriptor).
#
# data/restraints.csv -> _ihm_cross_link_restraint: 251 chain-resolved
# restraints (crosslink_id,chain1,chain2,granularity; 176 intra-, 75
# inter-molecular). Not restrained: 16 crosslinks with an endpoint in the
# unmodeled tag (chain E, seq_id 1-40), and self-link 161 (subunit 1
# K302-K302), which implies an inter-molecular contact that a single-copy
# model cannot represent.
#   * chain1/chain2 = _struct_asym.id.
#   * granularity -> model_granularity, set by the representation: by-residue
#     if both endpoints are in residue-level beads (ResidueCrossLink, 127),
#     by-feature if either is in a multi-residue bead (FeatureCrossLink, 124).
# --------------------------------------------------------------------------

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

# CHANGE FOR YOUR ENTRY: 3DEM fitting method (see em_restraint.fits below).
em_restraint = ihm.restraint.EM3DRestraint(
    dataset=em_data, assembly=assembly,
    fitting_method="Gaussian mixture models")
system.restraints.extend([crosslink_restraint, em_restraint])

# --------------------------------------------------------------------------
# CHANGE FOR YOUR ENTRY: sampling protocol and clustering post-processing.
#
# Writes: _ihm_modeling_protocol, _ihm_modeling_protocol_details, and
# _ihm_modeling_post_process. (Pass multi_scale=True to Step if
# _ihm_modeling_protocol_details.multi_scale_flag should be YES.)
# --------------------------------------------------------------------------

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
