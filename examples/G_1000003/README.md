# G_1000003: Batch-depositing a multi-entry collection with `ihmdep`

Unlike [`9A9W/`](../9A9W) and [`9A8W/`](../9A8W), which build an IHMCIF file
from coordinates and CSV tables with `python-ihm`, this directory starts with
three already-complete IHMCIF entries and their thumbnail images from a single
published collection (`PDBDEV_G_1000003`, Stahl et al., *Nat. Commun.* **15**,
7866, 2024; DOI [`10.1038/s41467-024-51771-2`][paper], PubMed `39251624`). It
shows how a multi-entry study is represented in IHMCIF and how to deposit,
monitor, submit, and retrieve a batch of entries from the shell with `ihmdep`.

```bash
cd examples/G_1000003             # from the repository root

for id in 9A40 9A6P 9A7U; do
    ihmdep --mode dev upload "$id.cif" --image "$id.png"
done > rids.txt

ihmdep --mode dev get_status --wait - < rids.txt &&
ihmdep --mode dev set_status --to SUBMIT --yes - < rids.txt &&
ihmdep --mode dev get_status --wait - < rids.txt &&
ihmdep --mode dev download --mmcif -o generated/ - < rids.txt
```

[paper]: https://doi.org/10.1038/s41467-024-51771-2

---

## What is in this directory

Each entry is an integrative model built with `AlphaLink2` v1.0 from photo-
crosslinking mass spectrometry (SDA) and deep learning:

| Entry | PDB-Dev ID | Chains | PRIDE | Crosslinks | Restraints | Atoms |
|---|---|---|---|---|---|---|
| `9A40` | `PDBDEV_00000221` | `A`–`F` | `PXD020453` | 1534 | 1534 | 49703 |
| `9A6P` | `PDBDEV_00000318` | `A`–`B` | `PXD035508` | 14 | 14 | 37831 |
| `9A7U` | `PDBDEV_00000359` | `A`–`B` | `PXD035508` | 13 | 13 | 21540 |

- **`9A40.cif` / `9A40.png`** (*"model of Cullin4"*): 6 single-copy entities —
  `CUL4A_HUMAN` (`Q13619`), `DCAF1_HUMAN` (`Q9Y4B6`), `DDB1_HUMAN` (`Q16531`),
  `RBX1_HUMAN` (`P62877`), `G7N4W9_MACMU` (`G7N4W9`), and `A4UDG5_SIV`
  (`A4UDG5`). Its 1534 identified crosslinks in `_ihm_cross_link_list` (1154
  intra-protein, 380 inter-protein; 0 self-links) map 1:1 to 1534 chain-
  resolved `by-residue` restraints in `_ihm_cross_link_restraint` (1154 intra-
  molecular, 380 inter-molecular).
- **`9A6P.cif` / `9A6P.png`** (*"Integrative model of RPOB-RPOC by crosslinking
  MS and deep learning"*): 2 single-copy entities — `RPOB_BACSU` (`P37870`) and
  `RPOC_BACSU` (`P37871`), with 14 inter-protein identified crosslinks and 14
  inter-molecular `by-residue` restraints.
- **`9A7U.cif` / `9A7U.png`** (*"Integrative model of RPOC-GREA by crosslinking
  MS and deep learning"*): 2 single-copy entities — `RPOC_BACSU` (`P37871`) and
  `GREA_BACSU` (`P80240`), with 13 inter-protein identified crosslinks and 13
  inter-molecular `by-residue` restraints.

Alongside the three input pairs (`9A40`, `9A6P`, `9A7U`), `reports/` holds
three pipeline-generated mmCIF files (`D_9-ENB2.cif`, `D_9-ENB4.cif`,
`D_9-ENB6.cif`) downloaded from a previous dev-server run of the batch loop
above (`9-ENB2` ← `9A40`, `9-ENB4` ← `9A6P`, `9-ENB6` ← `9A7U`). Comparing
`9A7U.cif` against `reports/D_9-ENB6.cif` shows what the deposition server's
post-`SUBMIT` processor rewrites: `data_9A7U` and `_entry.id` become the
deposition ID (`D_9-ENB6`), archive-release categories (`_database_2`,
`_pdbx_database_status`, `_pdbx_audit_revision_history`,
`_ihm_entry_collection*`) are replaced by the deposition workflow's own frame,
and `_audit_conform` is stamped with the server's active dictionary versions.

### How these entries complement `9A9W` and `9A8W`

Because `9A40.cif`, `9A6P.cif`, and `9A7U.cif` are full 40-category archive
entries rather than minimal outputs from a tutorial script, they are concrete
examples of the categories covered in the "Polishing for a real deposition"
sections of [`9A9W/README.md`](../9A9W/README.md) and
[`9A8W/README.md`](../9A8W/README.md). In `9A7U.cif`, for instance:

- **Primary citation and authors (`_citation`, `_citation_author`):**
  ```mmcif
  _citation.country	.
  _citation.id	1
  _citation.journal_abbrev	"Nat. Commun."
  _citation.journal_id_ASTM	.
  _citation.journal_id_CSD	.
  _citation.journal_id_ISSN	.
  _citation.journal_issue	.
  _citation.journal_volume	15
  _citation.page_first	7866
  _citation.page_last	.
  _citation.pdbx_database_id_DOI	10.1038/s41467-024-51771-2
  _citation.pdbx_database_id_PubMed	39251624
  _citation.title	"Modelling protein complexes with crosslinking mass spectrometry and deep learning"
  _citation.year	2024
  #
  loop_
  _citation_author.citation_id
  _citation_author.name
  _citation_author.ordinal
  1	"Stahl, K."	1
  1	"Warneke, R."	2
  # ... (8 rows total, through "Rappsilber, J.")
  ```
- **Modeling software (`_software`) linked to the protocol step:**
  ```mmcif
  _software.citation_id	1
  _software.classification	"model building"
  _software.description	"Modelling protein complexes with crosslinking mass spectrometry and deep learning"
  _software.location	https://github.com/Rappsilber-Laboratory/AlphaLink2
  _software.name	AlphaLink2
  _software.pdbx_ordinal	1
  _software.type	program
  _software.version	1.0
  ```
- **UniProt reference alignments (`_struct_ref`, `_struct_ref_seq`):**
  ```mmcif
  loop_
  _struct_ref.db_code
  _struct_ref.db_name
  _struct_ref.details
  _struct_ref.entity_id
  _struct_ref.id
  _struct_ref.pdbx_align_begin
  _struct_ref.pdbx_align_end
  _struct_ref.pdbx_db_accession
  _struct_ref.pdbx_db_isoform
  _struct_ref.pdbx_seq_one_letter_code
  A	UNP	.	1	1	1	.	P37871	.	MLDVNNFEYMNIGLASPDKIRSWSFGEVKKPETINYRTLKPEKDGLFCERIFGPTKDWECHCGKYKRVRYKGVVCDRCGVEVTRAKVRRERMGHIELAAPVSHIWYFKGIPSRMGLVLDMSPRALEEVIYFASYVVTDPANTPLEKKQLLSEKEYRAYLDKYGNKFQASMGAEAIHKLLQDIDLVKEVDMLKEELKTSQGQRRTRAIKRLEVLEAFRNSGNKPSWMILDVLPVIPPELRPMVQLDGGRFATSDLNDLYRRVINRNNRLKRLLDLGAPSIIVQNEKRMLQEAVDALIDNGRRGRPVTGPGNRPLKSLSHMLKGKQGRFRQNLLGKRVDYSGRSVIVVGPHLKMYQCGLPKEMALELFKPFVMKELVEKGLAHNIKSAKRKIERVQPEVWDVLESVIKEHPVLLNRAPTLHRLGIQAFEPTLVEGRAIRLHPLVCTAYNADFDGDQMAVHVPLSAEAQAEARILMLAAQNILNPKDGKPVVTPSQDMVLGNYYLTLERAGAVGEGMVFKNTDEALLAYQNGYVHLHTRVAVAANSLKNVTFTEEQRSKLLITTVGKLVFNEILPESFPYMNEPTKSNIEEKTPDRFFLEKGADVKAVIAQQPINAPFKKGILGKIIAEIFKRFHITETSKMLDRMKNLGFKYSTKAGITVGVSDIVVLDDKQEILEEAQSKVDNVMKQFRRGLITEEERYERVISIWSAAKDVIQGKLMKSLDELNPIYMMSDSGARGNASNFTQLAGMRGLMANPAGRIIELPIKSSFREGLTVLEYFISTHGARKGLADTALKTADSGYLTRRLVDVAQDVIIRETDCGTDRGILAKPLKEGTETIERLEERLIGRFARKQVKHPETGEVLVNENELIDEDKALEIVEAGIEEVWIRSAFTCNTPHGVCKRCYGRNLATGSDVEVGEAVGIIAAQSIGEPGTQLTMRTFHTGGVAGDDITQGLPRIQELFEARNPKGQATITEIDGTVVEINEVRDKQQEIVVQGAVETRSYTAPYNSRLKVAEGDKITRGQVLTEGSIDPKELLKVTDLTTVQEYLLHEVQKVYRMQGVEIGDKHVEVMVRQMLRKVRVIDAGDTDVLPGTLLDIHQFTEANKKVLLEGNRPATGRPVLLGITKASLETDSFLSAASFQETTRVLTDAAIKGKRDELLGLKENVIIGKLVPAGTGMMKYRKVKPVSNVQPTDDMVPVE
  B	UNP	.	2	2	1	.	P80240	.	MAQEKVFPMTAEGKQKLEQELEYLKTVKRKEVVERIKIARSFGDLSENSEYDSAKEEQAFVEGRVTTLENMIRNAKIIEDDGGSNVVGLGKTVTFVELPDGDEESYTIVGSAEADPFEGKISNDSPIAKSLLGKKVDEEVTVQTPGGEMLVKIVKIS
  #
  loop_
  _struct_ref_seq.align_id
  _struct_ref_seq.db_align_beg
  _struct_ref_seq.db_align_end
  _struct_ref_seq.ref_id
  _struct_ref_seq.seq_align_beg
  _struct_ref_seq.seq_align_end
  1	1	1199	1	1	1199
  2	1	157	2	1	157
  ```
- **Dataset groups (`_ihm_dataset_group`, `_ihm_dataset_group_link`):**
  ```mmcif
  _ihm_dataset_group.application	.
  _ihm_dataset_group.details	.
  _ihm_dataset_group.id	1
  _ihm_dataset_group.name	.
  #
  _ihm_dataset_group_link.dataset_list_id	1
  _ihm_dataset_group_link.group_id	1
  ```

---

## How collection membership is recorded in IHMCIF

When a publication deposits a benchmark set or a family of related complexes,
PDB-IHM can group the individual entries under a single collection ID (here
`PDBDEV_G_1000003`). In the archive files, two categories record that link —
`_ihm_entry_collection` defines the collection, and
`_ihm_entry_collection_mapping` maps the entry ID into it (shown here from
`9A7U.cif`):

```mmcif
_ihm_entry_collection.id	PDBDEV_G_1000003
_ihm_entry_collection.name	.
_ihm_entry_collection.details	'Collection of entries belonging to the publication: "Modelling protein complexes with crosslinking mass spectrometry and deep learning"'
#
_ihm_entry_collection_mapping.collection_id	PDBDEV_G_1000003
_ihm_entry_collection_mapping.entry_id	9A7U
```

`9A40.cif` and `9A6P.cif` carry the exact same `_ihm_entry_collection` block
and differ only in `_ihm_entry_collection_mapping.entry_id` (`9A40` and
`9A6P`). In `python-ihm`, an entry is added to a collection via
`system.collections.append(ihm.Collection(id=..., name=..., details=...))`.

---

## Step-by-step walkthrough of the batch deposition loop

Every `ihmdep` subcommand is designed to compose cleanly in shell pipelines:

1. **Upload each `.cif` and `.png` pair (`ihmdep upload`):**
   ```bash
   for id in 9A40 9A6P 9A7U; do
       ihmdep --mode dev upload "$id.cif" --image "$id.png"
   done > rids.txt
   ```
   - `--mode dev` targets the development server (`data-dev.pdb-ihm.org`,
     catalog `99`) rather than the live archive (`ihmdep` defaults to
     production).
   - `upload` transfers the `.cif` and `.png` to Hatrac, creates the
     `entry` row in ERMrest directly as `DEPO` (`--draft` would park it as
     `DRAFT` instead) so the backend processor starts immediately, and prints
     **only** the resulting RID (e.g., `9-ENB2`) on `stdout`.
   - If one file in the loop fails to upload, diagnostics go to `stderr` and
     nothing is written to `stdout`, so `rids.txt` collects only the RIDs that
     succeeded.

2. **Wait for initial processing (`ihmdep get_status --wait`):**
   ```bash
   ihmdep --mode dev get_status --wait - < rids.txt
   ```
   - Passing `-` tells `ihmdep` to read RIDs from `stdin` (whitespace- or
     newline-separated).
   - `--wait` polls every 60 seconds (configurable via `--interval SECS`) until
     no entry in the batch has a pending `Process_Status` (`New`, `Reprocess`,
     `In progress`, or `processing ...`).
   - It exits `0` only if **every** RID in `rids.txt` finishes with
     `Process_Status = Success` (at which point `Workflow_Status` is
     `RECORD READY`). If any entry errors, `get_status` exits `1`, short-
     circuiting the `&&` chain before `set_status --to SUBMIT` can run.

3. **Submit the batch (`ihmdep set_status --to SUBMIT --yes`):**
   ```bash
   ihmdep --mode dev set_status --to SUBMIT --yes - < rids.txt
   ```
   - `set_status --to SUBMIT` checks that every entry is currently in
     `Workflow_Status = RECORD READY` before transitioning any of them to
     `SUBMIT`.
   - `--yes` skips the interactive confirmation prompt so the pipeline can run
     unattended.

4. **Wait for post-submission processing and download (`ihmdep download`):**
   ```bash
   ihmdep --mode dev get_status --wait - < rids.txt &&
   ihmdep --mode dev download --mmcif -o generated/ - < rids.txt
   ```
   - Transitioning to `SUBMIT` triggers a second backend run that assigns the
     deposition accession (`D_<RID>`) and generates the archive mmCIF and
     validation reports.
   - Because the generated mmCIF is produced immediately after `SUBMIT` while
     full validation PDFs take longer, `--mmcif` downloads the processed
     mmCIF files (`D_<RID>.cif`) into `generated/` as soon as that stage
     completes. Omit `--mmcif` (or pass `--full --summary`) when you also want
     the validation PDFs, or pass `--logs` to fetch diagnostic logs from a
     failed run.

---

## Re-running on `dev`: MD5 deduplication and `--salt`

Both `ihmv` and `ihmdep` deduplicate uploads by MD5 checksum against entries
already owned by your account, and Hatrac object paths are content-addressed.
In production, that prevents accidental duplicate depositions: re-running the
upload loop after a network hiccup simply prints the existing RIDs.

When testing a batch script repeatedly on `dev`, however, deduplication has an
important consequence: once an entry has reached `SUBMIT`, re-uploading the
same unmodified `.cif` returns that **already-submitted** RID. The subsequent
`set_status --to SUBMIT` step then fails because the entry is already at
`SUBMIT` rather than `RECORD READY`.

To exercise a fresh end-to-end upload and submission on `dev` without editing
the input files by hand, pass `--salt` to `upload` (or `run`):

```bash
for id in 9A40 9A6P 9A7U; do
    ihmdep --mode dev upload "$id.cif" --image "$id.png" --salt
done > rids.txt
```

`--salt` writes temporary copies with a unique timestamp comment appended to
the `.cif` (`# ihmtools-salt: ...`) and a `tEXt` chunk added to the `.png`,
giving both files new filenames and new MD5 checksums so the server creates a
brand-new `DEPO` entry every time. There is no unsalted way to deposit the
same bytes twice: the server keeps one entry per file (and per file name) for
each account, so `--force` has no effect.

---

## Inspecting status and filtering batches from the shell

### Two status columns (`--workflow` and `--process`)

The deposition catalog tracks two independent status columns on every entry:

- `Workflow_Status`: where the entry sits in the deposition lifecycle (`DRAFT`,
  `DEPO`, `RECORD READY`, `SUBMIT`, `HOLD`, `RELEASE READY`, `REL`).
- `Process_Status`: the outcome of the most recent backend run (`New`,
  `In progress`, `Success`, `Error: processing uploaded mmCIF file`, etc.).

Querying a single RID prints both separated by `; `, whereas `--workflow` or
`--process` selects a single field for command substitution:

```bash
$ ihmdep --mode dev get_status 9-ENB6
SUBMIT; Success

$ ihmdep --mode dev get_status 9-ENB6 --workflow
SUBMIT

$ ihmdep --mode dev get_status 9-ENB6 --process
Success
```

### Exit codes

`get_status` encodes the aggregate state of the queried RID(s) in its exit
status:

| Exit code | Meaning |
|---|---|
| `0` | All queried entries finished (`Success`) |
| `1` | At least one entry failed (`Error...`, or `Workflow_Status` `ERROR`) |
| `2` | At least one entry is still running, queued or `DRAFT` — this outranks a failure, so a batch keeps polling |
| `3` | An unknown RID was requested |

### Filtering tab-separated listings

With no RID arguments, or with several, `ihmdep get_status` prints a table.
On a terminal the columns are padded for human reading; when piped or
redirected, output is strictly **tab-separated** with a `#`-prefixed header
line. The columns depend on the form:

- no RIDs (the listing), or any RIDs with `-v`: `RID`, `MODIFIED`,
  `ACCESSION`, `WORKFLOW`, `PROCESS`, `FILE`; under `-v` each row's
  `Record_Status_Detail` follows it on `stderr`;
- several RIDs: `RID`, `WORKFLOW`, `PROCESS`.

Because the status columns and file names can contain spaces, always split
on `\t`:

```bash
# List RIDs of entries whose backend run failed:
ihmdep --mode dev get_status | awk -F'\t' '!/^#/ && $5 ~ /^Error/ {print $1}'

# List RIDs in rids.txt that are RECORD READY and ready to SUBMIT:
ihmdep --mode dev get_status - < rids.txt |
    awk -F'\t' '!/^#/ && $2 == "RECORD READY" && $3 == "Success" {print $1}'
```
