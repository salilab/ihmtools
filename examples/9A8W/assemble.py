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
  * a crosslink onto a coarse bead cannot name a residue, so restraints come
    in two granularities and restraints.csv says which

This is a worked example for one entry, not a general converter.
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

# SDA is a short crosslinker; 22 A is the bound the study scored against, and
# psi/sigma are the Bayesian nuisance parameters IMP fitted alongside it.
CROSSLINK_DISTANCE = 22.0
CROSSLINK_PSI = 0.25
CROSSLINK_SIGMA = 2.0

# --------------------------------------------------------------------------
# Read the model. gemmi.read_structure() is no use here -- _atom_site is empty
# -- so read the categories directly.
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
# polymeric -- protein or DNA -- so there is no ligand case to handle.
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

system = ihm.System(title="Integrative structure of human SNAPc-DNA")
system.entities.extend(entities.values())
system.asym_units.extend(asyms.values())

assembly = ihm.Assembly(list(asyms.values()), name="Modeled assembly")

# --------------------------------------------------------------------------
# The spheres, and the representation they imply.
#
# A one-residue sphere is a residue-level bead: those regions were built from
# known structures and sampled as rigid bodies. A sphere spanning several
# residues is a coarse bead standing in for a region with no structure, left
# flexible. Reading the spans back out reconstructs both kinds of segment.
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
# The data 9A8W was built from. The crosslinking MS is fully deposited in
# PRIDE, which is what lets a validation report check the restraints against
# the primary data rather than taking the entry's word for them.
# --------------------------------------------------------------------------

crosslink_data = ihm.dataset.CXMSDataset(ihm.location.PRIDELocation("PXD053341"))
em_data = ihm.dataset.EMDensityDataset(ihm.location.EMDBLocation("EMD-50730"))
system.orphan_datasets.extend([
    crosslink_data, em_data,
    ihm.dataset.DeNovoModelDataset(ihm.location.ModelArchiveLocation("ma-pgtjz")),
    ihm.dataset.PDBDataset(ihm.location.PDBLocation("9FSO")),
    ihm.dataset.PDBDataset(ihm.location.PDBLocation("7ZX8")),
    ihm.dataset.PDBDataset(ihm.location.PDBLocation("7XUR")),
])

# --------------------------------------------------------------------------
# Crosslinks. As in 9A9W the measurements and the modelled pairings are
# separate files, but here a pairing also says which granularity it used:
# a bead covering ten residues cannot be restrained at one of them.
# --------------------------------------------------------------------------

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

em_restraint = ihm.restraint.EM3DRestraint(
    dataset=em_data, assembly=assembly,
    fitting_method="Gaussian mixture models")
system.restraints.extend([crosslink_restraint, em_restraint])

# --------------------------------------------------------------------------
# How the model was sampled: replica-exchange Monte Carlo, then clustered.
# --------------------------------------------------------------------------

protocol = ihm.protocol.Protocol(name="Modeling")
protocol.steps.append(ihm.protocol.Step(
    assembly=assembly, dataset_group=None, name="Sampling",
    method="Replica exchange monte carlo",
    num_models_begin=0, num_models_end=320000))

analysis = ihm.analysis.Analysis()
analysis.steps.append(ihm.analysis.ClusterStep(
    feature="RMSD", num_models_begin=320000, num_models_end=8260))
protocol.analyses.append(analysis)
system.orphan_protocols.append(protocol)


class Model(ihm.model.Model):
    """A sphere model: get_spheres() rather than get_atoms()."""

    def get_spheres(self):
        for chain, begin, end, x, y, z, radius in spheres:
            yield ihm.model.Sphere(
                asym_unit=asyms[chain], seq_id_range=(begin, end),
                x=x, y=y, z=z, radius=radius)


model = Model(assembly=assembly, protocol=protocol,
              representation=representation, name="Best scoring model")
em_restraint.fits[model] = ihm.restraint.EM3DRestraintFit()

group = ihm.model.ModelGroup([model], name="Cluster 0")
system.state_groups.append(ihm.model.StateGroup([ihm.model.State([group])]))

# The deposited model represents a cluster of 5766 sampled models.
system.ensembles.append(ihm.model.Ensemble(
    model_group=group, num_models=5766, name="Cluster 0",
    clustering_method="Density based threshold-clustering",
    clustering_feature="RMSD"))

with open(OUTPUT, "w") as fh:
    ihm.dumper.write(fh, [system])
print("wrote %s" % OUTPUT)

with open(OUTPUT) as fh:
    check, = ihm.reader.read(fh)
crosslinks = check.restraints[0]
built = [m for sg in check.state_groups for st in sg for g in st for m in g][0]
print("%d entities, %d spheres, %d measurements, %d restrained"
      % (len(check.entities), len(built._spheres),
         sum(len(g) for g in crosslinks.experimental_cross_links),
         len(crosslinks.cross_links)))
