version = '0.4.1'  # REDUNDANT: MAKE SURE THIS MATCHES THE VERSION setup.py
version_notes = ('Review-round-1 fixes to the 0.4.0 Linux in-process path: CLI failures '
                 '(paramiko.SSHException / socket.gaierror / SystemExit / any BaseException '
                 'except KeyboardInterrupt) are now written into the captured stderr as a '
                 'formatted traceback -- the exact frozen-exe contract -- so the '
                 'aview_hpc.aview_hpc wrapper raises its usual RuntimeError and CDM retry '
                 'handlers (hpc_jobs.py / base_test.py, which catch only RuntimeError) keep '
                 'working; and sys.excepthook + root-logger level, which _cli.main() mutates, '
                 'are saved and restored around every in-process call so the host Adams View '
                 'process is unchanged after client calls. Unit tests added for both.')
date = 'October, 8th 2026'
