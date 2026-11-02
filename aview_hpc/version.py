version = '0.6.0'  # REDUNDANT: MAKE SURE THIS MATCHES THE VERSION setup.py
version_notes = ('Site configuration moved out of code: LSF bsub/bjobs paths, default queue, '
                 'default resource requirement and farm-visible storage roots are read from '
                 '~/.aview_hpc keys (bsub, bjobs, queue, res_req, farm_roots) or AVIEW_HPC_* '
                 'environment variables, with neutral defaults. Committed logs/results removed; '
                 'denylist check added.')
date = 'November, 2nd 2026'
