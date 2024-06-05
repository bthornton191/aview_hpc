# aview_hpc

Submit simulations to an HPC cluster directly from Adams View!

> [!WARNING]
> Many features currently only work with the [slurm](https://slurm.schedmd.com/) job scheduler.

## Installation

Install using pip:
```shell
pip install git+https://github.com/bthornton191/aview_hpc
```

> [!IMPORTANT]
> pip will not install the binary. Download the binary [here](https://github.com/bthornton191/aview_hpc/releases/latest/download/main.exe)
> and place it inside the `aview_hpc` package directory. 


## Configuration

```shell
python -m aview_hpc set_config --host <host>
python -m aview_hpc set_config --user <user>
python -m aview_hpc set_config --submit_cmd <submit_cmd>
python -m aview_hpc set_config --remote_tempdir <remote_tempdir>
```

Where 
- `<host>` is the hostname of the HPC cluster
- `<user>` is the username on the HPC cluster
- `<submit_cmd>` is the command to submit a job on the HPC cluster (see below)
- `<remote_tempdir>` is a directory on the HPC cluster where the simulation files will be copied to

To securely store the password, use the `keyring` package:
```shell
python -m keyring set aview_hpc <user>
```
This will prompt you to enter your hpc password.

### Submit Command

The submit command **MUST** 

1. Take the path to the Adams Solver Command (.acf) file as the first positional argument. 
2. Return the the text "submitted batch job <job_id>" where `<job_id>` is the job id of the submitted job. 

This likely means you will need a custom submission script. See [slurm.py](hpc_scripts/slurm.py) for an example.

> [!TIP]
> The submit command can take any arbitrary keyword arguments.


## Development

### Building the Binary
```bat
git clone https://github.com/bthornton191/aview_hpc
cd aview_hpc
python -m virtualenv env
env\Scripts\activate.bat
pip install -r requirements.txt
pyinstaller --noconfirm main.spec & mv dist\main.exe aview_hpc\main.exe
```

### Testing

> [!WARNING]
> The test suite actually runs jobs on the HPC cluster. You must configure the `aview_hpc` package 
> with the correct HPC credentials before running the tests. See the Configuration section above.
 
