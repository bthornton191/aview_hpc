version = '0.5.0'  # REDUNDANT: MAKE SURE THIS MATCHES THE VERSION setup.py
version_notes = ('LocalTransport: HPCSession remote operations (exec, file '
                 'put/get/copy, mkdtemp, listdir) moved behind a transport '
                 'seam. SSHTransport keeps the paramiko behaviour unchanged '
                 '(paramiko now imported lazily, only on that path); '
                 'LocalTransport runs scheduler commands as argv lists with '
                 'subprocess.run(shell=False), moves files with shutil and '
                 'makes job dirs under the configured remote_tempdir, '
                 'refusing non-farm-visible roots like /tmp. Selection: new '
                 "'transport' key in ~/.aview_hpc (local|ssh), else local "
                 'only when the configured host resolves to this machine. '
                 'For the sjlsf01 submit-host VM case (sjcvl-thornton), '
                 'where 0.4.x SSHed from the VM to itself.')
date = 'October, 8th 2026'
