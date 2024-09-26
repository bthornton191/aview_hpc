# Setup
Follow the steps below to setup any jof the scripts in this directory for convenient usage on an hpc.

1. Download script.py and put it somewhere on the hpc system (e.g. ~/scripts)
2. Run `chmod +x /path/to/script.py`
3. Add alias `asub="/path/to/script.py"` to your ~/.bashrc script
4. Log out and back in for the alias to take effect

Now you can submit using `asub model.acf` and it will recognize the `NTHREADS` setting in the .adm file and 
set the ncpus accordingly in the slurm script.


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
