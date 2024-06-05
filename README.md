# aview_hpc

Submit simulations to an HPC cluster directly from Adams View!

## Installation
```shell
python -m pip install git+https://github.com/bthornton191/aview_hpc
```

## Configuration

```shell
python aview_hpc.py set_config --host <hpc_hostname> --user <hpc_username> --remote_tempdir <remote_tempdir>
```

> [!NOTE]
> `remote_tempdir` is the directory on the HPC cluster where the temporary files will be stored. 


To securely store the password, use the `keyring` package:
```shell
python -m keyring set aview_hpc <hpc_username>
```
This will prompt you to enter your hpc password.


## Development

> [!WARNING]
> The test suite actually runs jobs on the HPC cluster. You must configure the `aview_hpc` package 
> with the correct HPC credentials before running the tests. See the Configuration section above.
 