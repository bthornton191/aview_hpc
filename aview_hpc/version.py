version = '0.5.6'  # REDUNDANT: MAKE SURE THIS MATCHES THE VERSION setup.py
version_notes = ('Bounded transport timeouts (t_a42da936): SSH connect/banner/auth timeouts, '
                 'keepalive, per-channel I/O timeout on exec and SFTP, and a wall-clock cap '
                 '(AVIEW_HPC_CLI_TIMEOUT_S, default 1800 s) on the Windows exe subprocess '
                 'that kills the process tree and raises TimeoutError. A get_results hung '
                 '~20 h on 2026-10-26 after the server rebooted mid-session.')
date = 'October, 9th 2026'
