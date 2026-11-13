"""Command-line tools for the PDB-IHM validation and deposition systems.

`ihmv` submits structures to the validation catalog and `ihmdep` deposits
entries. Both are built on deriva-py (ERMrest, Hatrac, Globus login), and what
they have in common -- authentication, uploads, polling and the CLI
conventions -- lives once, in `_common`.
"""

__version__ = "0.0.1a14"
