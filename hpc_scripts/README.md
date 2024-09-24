
# slurm.py
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

> [!WARNING]
> slurm.py uses the `FILE` command at the top of the acf file to determine the name of the adm file.
> This will **FAIL** if you provide additional arguments to the `FILE` command (e.g `FILE/MODEL=name, OUTPUT=name_out`) 
