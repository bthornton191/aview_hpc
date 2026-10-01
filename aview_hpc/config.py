import json
from pathlib import Path

import keyring

CONFIG_FILE = Path.home() / '.aview_hpc'


def get_config():
    """Get the configuration for the HPC cluster"""
    if not CONFIG_FILE.exists():
        config = {}

    else:
        with open(CONFIG_FILE) as f:
            config = json.load(f)

    return config


def set_config(host=None, username=None, password=None, **kwargs):
    """Set the configuration for the HPC cluster.

    Keys whose argument is ``None`` are left unchanged: partial updates
    (e.g. successive ``python -m aview_hpc set_config --key value`` calls)
    must not wipe keys set by earlier calls.
    """
    config = get_config()
    config['host'] = host or config.get('host', None)
    config['username'] = username or config.get('username', None)

    # All other kwargs (None values are skipped -- see docstring)
    for k, v in kwargs.items():

        if v is None:
            continue

        if isinstance(v, Path):
            v = v.as_posix()

        config[k] = v

    if password is not None and config['username'] is not None:
        keyring.set_password('aview_hpc', config['username'], password)
    elif password is not None:
        raise ValueError('A username must be provided to set a password')

    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)
