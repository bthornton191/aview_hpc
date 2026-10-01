# Setup
Follow the steps below to setup any jof the scripts in this directory for convenient usage on an hpc.

1. Download <script>.py and put it somewhere on the hpc system (e.g. ~/scripts)
2. Modify as necessary.
3. Run `chmod +x /path/to/script.py`
4. Add `alias asub="/path/to/script.py"` to your ~/.bashrc script
5. Log out and back in for the alias to take effect

Now you can submit using `asub model.acf` and it will recognize the `NTHREADS` setting in the .adm file and 
set the ncpus accordingly in the job script.


# Scripts
## slurm.py
Submit an ACF file to the cluster using SLURM

### Usage
```
asub.py <acf_file> [options]
positional arguments:
  acf_file              Path to the ACF file

optional arguments:
  -h, --help            show this help message and exit
  --acar                Use acar solver
  --mins MINS           Number of minutes for job execution (default: 120)
```  
### Notes
- This will recognize the following:
    * The .adm file from the .acf file
    * The NTHREADS option in the .adm file

> [!CAUTION]
> slurm.py uses the `FILE` command at the top of the acf file to determine the name of the adm file.
> This will **FAIL** if you provide additional arguments to the `FILE` command (e.g `FILE/MODEL=name, OUTPUT=name_out`)

## lsf.py
Submit an ACF file to the cluster using LSF (written for the Cadence sjlsf01 farm,
IBM Spectrum LSF 10.1.0.15, verified 2026-10-01). Submits through the site wrapper
`/grid/sfi/farm/bin/bsub`, which enforces the farm rules (mandatory `-W` and `-P`).

### Usage
```
lsf.py <acf_file> [options]
positional arguments:
  acf_file              Path to the ACF file

optional arguments:
  --mins MINS           Wall-clock limit in minutes (default: 720)
  --queue QUEUE         LSF queue (default: lnx64)
  --project PROJECT     Charge code PRODUCT:RELEASE:GROUP:PURPOSE (required; or $LSF_PROJECT)
  --res_req RES_REQ     Resource requirement string (default: RHEL 8.6+/9 pin)
  --adams_home DIR      Adams install dir containing `mdi` (required; or $ADAMS_HOME)
  --license LICENSE     MSC license string, e.g. 1700@sjflex5 (required; or $MSC_LICENSE_FILE)
  --ld_library_path P   Optional LD_LIBRARY_PATH for the solver
  --email EMAIL         bsub -u value (default: noemail)
  --dry_run             Print the bsub command line and job script instead of submitting
```
### Notes
- Same acf/adm/NTHREADS recognition as slurm.py — the same CAUTION about the `FILE` command
  applies (it is acf-file parsing, not scheduler-specific).
- `NTHREADS` in the .adm file becomes `bsub -n N`, and `span[hosts=1]` is appended to the
  resource requirement as ONE combined `-R` string (e.g. `-R 'select[...] span[hosts=1]'`)
  so all threads land on one host — this LSF build rejects multiple `-R` options when a
  `span`/`cu`/`affinity` section is involved.
- The job script is written next to the acf file as `<name>.lsf` (unique-ified with a
  `_N` suffix on resubmit) — this is the glob the client's `resubmit_job` cleans up —
  and is fed to bsub via stdin (`bsub ... < name.lsf`). bsub has no script-file
  argument: a trailing filename is executed as the job COMMAND, which exits 127.
- On success, bsub prints `Job <id> is submitted to queue <queue>.`, which is what the
  aview_hpc LSF backend parses.
- Run under the host's python3 (RHEL 9 ships 3.9; the script is stdlib-only).

> [!CAUTION]
> If you pass `--res_req` through the aview_hpc client (which forwards keyword arguments by
> joining them into one shell command line), the value must not contain unquoted shell
> metacharacters — `select[(...)]` contains parentheses and `&&`/`||` that the remote shell
> would interpret. Either rely on the built-in default `--res_req` (recommended) or single-quote
> the value in the remote-side submit command.
