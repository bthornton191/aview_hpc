#! /usr/bin/python3
'''Submit an ACF file to the cluster using SLURM

usage: asub.py <acf_file> [options]

positional arguments:
  acf_file              Path to the ACF file

optional arguments:
  -h, --help            show this help message and exit
  --acar                Use acar solver
  --mins MINS           Number of minutes for job execution (default: 120)
  
note:
  This will recognize the following:
    * The .adm file from the .acf file
    * The NTHREADS option in the .adm file
'''

from contextlib import contextmanager
import os
import re
from pathlib import Path
import argparse
import sys
import traceback as tb

SLURM_SCRIPT = """#!/bin/bash
export LC_ALL=C

#Add line "#SBATCH" to add slurm option in script
#Add ## to comment out slurm option

export MSC_OS_PREF=rhe79
export LD_LIBRARY_PATH=/opt/hexagon/Adams/2022_1_875404/lib64
export MSC_LICENSE_FILE=1700@10.20.0.10

#Specify partition you would like to run your jobs
#medium spec: AMD EPYC 7443P-24cores with 1TB memory x 4
##big spec: AMD EPYC 7343-32cores with 2TB memory x 1

#Partition(Queue) Name please use -p option if not working as intended
#SBATCH -p medium
##SBATCH -p big

#Job Name
#SBATCH -J {job_name}


#standard output
#SBATCH -o log

#error output
#SBATCH -e err

#occupy one node
##SBATCH --exclusive

#Use this option when jobs to be run over nodes
#SBATCH --nodes=1

#Add command and option to run target software
/opt/hexagon/adams/2023_3/mdi -c ru-s i {acf_file} exit
"""

RE_MODEL = re.compile(r'file/.*model[ \t]*=[ \t]*(.+)[ \t]*(?:,|$)', flags=re.I|re.MULTILINE)
RE_NTHREADS = re.compile(r'nthreads[ \t]*=[ \t]*(\d+)\b', flags=re.I)

def get_adm_from_acf(acf_file: Path):
    text = acf_file.read_text()

    try:
        line = next(l for l in text.splitlines())
    except StopIteration:
        raise ValueError(f'{acf_file} has no contents!')

    if line.strip() != '':
        file = line.strip()

    else:

        try:
            file = next(m for m in RE_MODEL.findall(text))
        except StopIteration:
            raise ValueError(f'No model name was found in {acf_file}')

    adm_file = Path(file)
    if not adm_file.suffix:
        adm_file = adm_file.with_suffix('.adm')

    return acf_file.parent / adm_file    

def get_n_cpus(adm_file: Path):
    text = adm_file.read_text()
    try:
        n_threads = int(next(m for m in RE_NTHREADS.findall(text)))
    except StopIteration:
        # Set n_threads to 1 if not specified in .adm file
        n_threads = 1

    return n_threads

def get_unique_file_name(filename: Path):
    new_name = filename
    for i in range(999):
        
        if not new_name.exists():
            break
        
        new_name = filename.parent / f'{filename.stem}_{i}{filename.suffix}'
    
    return new_name

def submit(acf_file: Path, mins:int=120, args: list =None):
    job_name = acf_file.stem
    n_cpus = get_n_cpus(get_adm_from_acf(acf_file))
    script = SLURM_SCRIPT.format(job_name=job_name, acf_file=Path(acf_file.name))
    
    script_file = get_unique_file_name(Path(acf_file.with_suffix('.slurm').name))
    script_file.write_text(script)

    with cwd_as(acf_file.parent):
        cmd = f'sbatch --time={mins} --cpus-per-task={n_cpus} {script_file}'
        if args:
            cmd += ' ' + ' '.join(args)
            
        print(f'Running: {cmd}')
        os.system(cmd)

@contextmanager
def cwd_as(cwd: Path):
    _cwd = Path.cwd()
    os.chdir(cwd)

    try:
        yield
    finally:
        os.chdir(_cwd)

def excepthook(type, value, traceback):
    print(''.join(tb.format_exception(type, value, traceback)))
    sys.exit(1)


 
if __name__ == '__main__':

    sys.excepthook = excepthook
    
    parser = argparse.ArgumentParser(
        usage='%(prog)s <acf_file> [options]',
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter
        )
    parser.add_argument('acf_file', type=str, help='Path to the ACF file')
    parser.add_argument('--mins', 
                        type=int, 
                        default=60*12, 
                        help='Number of minutes for job execution (default: 120)')
    args, other_args = parser.parse_known_args()

    acf_file = Path(args.acf_file).absolute()
    mins = args.mins

    with cwd_as(acf_file.parent):
        submit(acf_file, mins=mins, args=other_args)

    
