version = '0.3.0'  # REDUNDANT: MAKE SURE THIS MATCHES THE VERSION setup.py
version_notes = ('Add LSF scheduler support (IBM Spectrum LSF, e.g. the Cadence sjlsf01 farm) '
                 'alongside Slurm, selected via the `scheduler` config key (default: slurm). '
                 'Add SSH key authentication for hosts without a stored keyring password, '
                 'a new hpc_scripts/lsf.py server-side submit script, and scheduler-backend unit tests.')
date = 'October, 1st 2026'
