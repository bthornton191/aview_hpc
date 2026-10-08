# aview_hpc

Submit simulations to an HPC cluster directly from Adams View!

> [!NOTE]
> Windows and Linux are both supported, with the [Slurm](https://slurm.schedmd.com/) and
> [LSF](https://www.ibm.com/products/hpc-lsf) job schedulers. Features that do not depend
> on the scheduler (file transfer, remote directory status, result download) work with
> either scheduler.

On Windows the library shells out to a frozen executable (downloaded automatically,
see below). On Linux there is no frozen executable: the library runs its CLI
in-process in the interpreter that imported it. Inside Adams View on Linux that is
Adams' embedded Python, so installing the package into that interpreter's dependency
target (e.g. with `pip --target`, hooked via a `.pth`) is what makes the
`aview_hpc` API work from Adams.

## Installation

Install using pip:
```shell
pip install git+https://github.com/bthornton191/aview_hpc
```

The package requires a binary file that will not be installed by pip. This will be automatically 
downloaded the first time the library is used. Download the binary 
[here](https://github.com/bthornton191/aview_hpc/releases/latest/download/aview_hpc.exe)
and place it inside the `aview_hpc` package directory. 

(Windows only. On Linux the library runs its CLI in-process and no binary is used.)


## Configuration

```shell
python -m aview_hpc set_config --host <host>
python -m aview_hpc set_config --username <user>
python -m aview_hpc set_config --submit_cmd <submit_cmd>
python -m aview_hpc set_config --remote_tempdir <remote_tempdir>
python -m aview_hpc set_config --scheduler <slurm|lsf>
python -m aview_hpc set_config --transport <ssh|local>
```

Where 
- `<host>` is the hostname of the HPC cluster
- `<user>` is the username on the HPC cluster
- `<submit_cmd>` is the command to submit a job on the HPC cluster (see below)
- `<remote_tempdir>` is a directory on the HPC cluster where the simulation files will be copied to
- `<scheduler>` is the job scheduler on the host: `slurm` (the default) or `lsf`
- `<transport>` is how the client reaches the scheduler host: `ssh` (paramiko; the
  historical behaviour) or `local` (run scheduler commands directly with no SSH).
  When the key is not set, aview_hpc uses `local` only if the configured `host`
  resolves to the machine the client is running on (the LSF submit-host case, e.g.
  the sjcvl-thornton qual VM), otherwise `ssh`. The choice is logged.

The scheduler key controls how aview_hpc parses job submissions and job tables:

- **slurm** (default): unchanged historical behavior. Submit responses are matched against
  `submitted batch job <id>`, the job table is built from `sacct`.
- **lsf**: submit responses are matched against `Job <id> is submitted`, the job table is built
  from `bjobs -a -o ... -json` and normalized to the same columns/states as the slurm table
  (`PEND/RUN/DONE/EXIT` become `PENDING/RUNNING/COMPLETED/FAILED`). Note that `bjobs -a` only
  reports jobs that are pending/running/suspended or finished *recently* (the cluster's
  `CLEAN_PERIOD`, commonly on the order of an hour) — older history requires `bhist` and is
  not included in the table. For jobs still running, LSF reports a *projected* finish time
  (unlike slurm, which reports unknown) — use `State`, not `End`, to decide whether a job
  is still running.

### Transport (local vs SSH)

Since 0.5.0 the client's remote operations go through a transport layer:

- **ssh** (default for remote hosts): paramiko SSH + SFTP, unchanged from earlier
  versions. paramiko is imported lazily, only on this path.
- **local** (the submit-host case): scheduler commands run directly as argument
  lists (`subprocess.run(..., shell=False)` — never a joined shell string), file
  transfer uses `shutil`, and job directories are created under `remote_tempdir`
  with `tempfile`. No SSH connection is made and paramiko is never imported.

`local` mode requires `remote_tempdir` to be set (explicitly or from `~/.aview_hpc`)
and to be on farm-visible storage — a job directory on local-only disk (e.g.
`/tmp` on the submit host) is invisible to the LSF execution hosts, so aview_hpc
refuses such a root (and refuses to run without one at all, rather than falling
back to the system temp dir) instead of submitting a job that can never run.
Use e.g. `/vols/<user>_space/aview_hpc`.

### Authentication

Two methods are supported, tried in this order (ssh transport only):

1. **SSH key authentication** (used when no password is stored in the keyring): aview_hpc
   lets paramiko look for keys in `~/.ssh` and in the SSH agent. A non-default key can be
   set with `python -m aview_hpc set_config --key_filename <path>`.
2. **Password**: stored with the `keyring` package:
   ```shell
   python -m keyring set aview_hpc <user>
   ```
   This will prompt you to enter your hpc password.

### submit_cmd

The submit command **MUST** 

1. Take the path to the Adams Solver Command (.acf) file as the first positional argument. 
2. Return the the text "submitted batch job <job_id>" where `<job_id>` is the job id of the submitted job. 

This likely means you will need a custom submission script. See [slurm.py](hpc_scripts/slurm.py) 
for a Slurm example, or [lsf.py](hpc_scripts/lsf.py) for an LSF example.

> [!TIP]
> The submit command can take any arbitrary keyword arguments. For LSF, `--mins`, `--queue`,
> `--project`, `--adams_home` and `--license` are forwarded to `bsub` (see
> [lsf.py](hpc_scripts/lsf.py)).

## Usage

### Submitting a Job within Adams View

1. Write the acf and adm files for the simulation.
2. Run the following
```python
from aview_hpc import submit
from pathlib import Path

submit(
    ## Required, path to the acf file
    acf=Path('path/to/model.acf'),
    
    ## Optional, leave commented to determine the adm from the acf
    # adm=Path('path/to/model.adm'), 
    
    ## Optional, additional files to copy to the HPC cluster (e.g. dll, xmt, etc)
    # aux_files=[], 

    ## Optional, any additional keyword arguments to pass to the hpc submit command
    # mins=120
)
```

## Development

### Releasing

Releases are automated: push a version tag and a GitHub Actions workflow
([release.yml](.github/workflows/release.yml)) builds `aview_hpc.exe` on a
Windows runner and attaches it to the GitHub Release for that tag. The
Windows client's `get_binary()` downloads exactly that asset, so a tag
without a built exe leaves every Windows client on that version broken.

To release a version:

1. Bump `version` in `aview_hpc/version.py` and `VERSION` in `setup.py`
   (they must match; the workflow fails the build if they don't match the
   tag).
2. Commit, push to `master`, then tag and push the tag:
   ```shell
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
3. The workflow checks out the tag tree, runs the pure unit tests
   (`test.test_schedulers`, `test.test_platform`), freezes the exe with
   PyInstaller (the same `main.spec` as `freeze.bat`), smoke-tests
   `aview_hpc.exe version` against the tag, and creates the Release with
   `aview_hpc.exe` attached.

To backfill a Release for an existing tag (e.g. a tag cut before the
workflow existed), run the workflow manually:
**Actions → release → Run workflow →** enter the tag (e.g. `v0.4.1`). The
build always uses the tag's tree, not the branch tip.

### Building the Binary

The workflow above builds and releases the exe automatically. To build one
by hand (for local debugging):
```bat
git clone https://github.com/bthornton191/aview_hpc
cd aview_hpc
python -m virtualenv env
env\Scripts\activate.bat
pip install -r requirements.txt
freeze.bat
```

### Testing

> [!WARNING]
> The test suite actually runs jobs on the HPC cluster. You must configure the `aview_hpc` package 
> with the correct HPC credentials before running the tests. See the Configuration section above. 

The `test_schedulers` and `test_platform` modules are exceptions: they are pure unit-test
suites (captured fixture text / mocked dispatch, no cluster required) and can always be run:
```shell
python -m unittest test.test_schedulers -v
python -m unittest test.test_platform -v
```

### Smoke test against the Cadence sjlsf01 LSF farm (manual)

This procedure verifies the LSF path end to end against the real farm, using a
5-minute trivial job. **A valid `-P` charge code must be supplied by the user** —
bsub rejects submissions without one. All steps except the last are safe,
read-only checks.

1. Copy `hpc_scripts/lsf.py` to the submit host and make it executable:
   ```shell
   ssh thornton@sjcvl-thornton.cadence.com 'mkdir -p ~/scripts'
   scp hpc_scripts/lsf.py thornton@sjcvl-thornton.cadence.com:~/scripts/lsf.py
   ssh thornton@sjcvl-thornton.cadence.com 'chmod +x ~/scripts/lsf.py'
   ```
2. Dry-run the submit script (prints the bsub command line and job script,
   submits nothing):
   ```shell
   ssh thornton@sjcvl-thornton.cadence.com \
     'python3 ~/scripts/lsf.py /home/thornton/hpc_tmp/smoke.acf --mins 5 \
       --project <PRODUCT:RELEASE:GROUP:PURPOSE> \
       --adams_home /home/thornton/adams/2023_4_1 \
       --license 1700@sjflex5 --dry_run'
   ```
3. Configure the client for LSF:
   ```shell
   python -m aview_hpc set_config --host sjcvl-thornton.cadence.com --username thornton
   python -m aview_hpc set_config --scheduler lsf
   python -m aview_hpc set_config --submit_cmd 'python3 /home/thornton/scripts/lsf.py'
   python -m aview_hpc set_config --remote_tempdir /home/thornton/hpc_tmp
   ```
   (No keyring password: key auth via `~/.ssh/id_ed25519` is used automatically.)
4. Verify the job table renders:
   ```shell
   python -m aview_hpc get_job_table
   ```
5. Submit the real 5-minute job (requires the `-P` code; the acf/adm pair for a
   trivial Adams model is up to the user):
   ```shell
   python -m aview_hpc submit smoke.acf --adm_file smoke.adm --mins 5 \
       --project <PRODUCT:RELEASE:GROUP:PURPOSE> \
       --adams_home /home/thornton/adams/2023_4_1 \
       --license 1700@sjflex5
   ```
 
