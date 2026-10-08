version = '0.4.0'  # REDUNDANT: MAKE SURE THIS MATCHES THE VERSION setup.py
version_notes = ('Linux client support: on POSIX the library API (submit / submit_multi / '
                 'get_results / get_job_table / get_remote_dir_status / resubmit_job) now runs '
                 'the CLI in-process instead of shelling out to the Windows-only frozen exe, '
                 'and all subprocess use is argv-list / shell=False with STARTUPINFO guarded '
                 'to Windows. Windows behavior is unchanged (same frozen exe). Adds unit '
                 'tests for the platform dispatch.')
date = 'October, 8th 2026'
