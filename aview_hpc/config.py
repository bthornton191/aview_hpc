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
    """Set the configuration for the HPC cluster"""
    config = get_config()
    config['host'] = host or config.get('host', None)
    config['username'] = username or config.get('username', None)

    # All other kwargs
    for k, v in kwargs.items():
        config[k] = v

    if password is not None and config['username'] is not None:
        keyring.set_password('aview_hpc', config['username'], password)
    elif password is not None:
        raise ValueError('A username must be provided to set a password')

    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)
