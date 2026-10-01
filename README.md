# aview_hpc

Submit simulations to an HPC cluster directly from Adams View!

> [!WARNING]
> Windows only, and the [Slurm](https://slurm.schedmd.com/) and [LSF](https://www.ibm.com/products/hpc-lsf)
> job schedulers are supported. Features that do not depend on the scheduler (file transfer,
> remote directory status, result download) work with either scheduler.

> [!WARNING]
> Currently supports Windows only.

## Installation

Install using pip:
```shell
pip install git+https://github.com/bthornton191/aview_hpc
```

The package requires a binary file that will not be installed by pip. This will be automatically 
downloaded the first time the library is used. Download the binary 
[here](https://github.com/bthornton191/aview_hpc/releases/latest/download/aview_hpc.exe)
and place it inside the `aview_hpc` package directory. 


## Configuration

```shell
python -m aview_hpc set_config --host <host>
python -m aview_hpc set_config --user <user>
python -m aview_hpc set_config --submit_cmd <submit_cmd>
python -m aview_hpc set_config --remote_tempdir <remote_tempdir>
python -m aview_hpc set_config --scheduler <slurm|lsf>
```

Where 
- `<host>` is the hostname of the HPC cluster
- `<user>` is the username on the HPC cluster
- `<submit_cmd>` is the command to submit a job on the HPC cluster (see below)
- `<remote_tempdir>` is a directory on the HPC cluster where the simulation files will be copied to
- `<scheduler>` is the job scheduler on the host: `slurm` (the default) or `lsf`

The scheduler key controls how aview_hpc parses job submissions and job tables:

- **slurm** (default): unchanged historical behavior. Submit responses are matched against
  `submitted batch job <id>`, the job table is built from `sacct`.
- **lsf**: submit responses are matched against `Job <id> is submitted`, the job table is built
  from `bjobs -a -o ... -json` and normalized to the same columns/states as the slurm table
  (`PEND/RUN/DONE/EXIT` become `PENDING/RUNNING/COMPLETED/FAILED`). Note that `bjobs -a` only
  reports jobs that are pending/running/suspended or finished *recently* (the cluster's
  `CLEAN_PERIOD`, commonly on the order of an hour) — older history requires `bhist` and is
  not included in the table.

### Authentication

Two methods are supported, tried in this order:

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

### Building the Binary
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

The `test_schedulers` module is the exception: it is a pure unit-test suite (captured fixture
text, no cluster required) and can always be run:
```shell
python -m unittest test.test_schedulers -v
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
   python -m aview_hpc set_config --host sjcvl-thornton.cadence.com --user thornton
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
 
