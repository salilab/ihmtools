"""Command-line tools for the PDB-IHM validation and deposition systems.

The two modules are deliberately self-contained: each talks to DERIVA's ERMrest
and Hatrac APIs directly, depends only on `requests`, and can be copied out and
run on its own. They duplicate their auth and HTTP layers rather than sharing
one, so a fix to either must be applied to both.
"""

__version__ = "0.0.1a13"
