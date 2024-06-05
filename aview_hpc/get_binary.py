import logging
import platform
from pathlib import Path

import requests

REPO_URL = 'https://github.com/bthornton191/aview_hpc'
BINARY_NAME = 'aview_hpc'
BINARY_URL = {
    'windows': f'{REPO_URL}/releases/latest/download/{BINARY_NAME}.exe',

    # Linux currently not supported
    'linux': None,
}
LOG = logging.getLogger(__name__)


def get_binary():

    url = BINARY_URL.get(platform.system().lower())

    if not url:
        raise Exception(f'Unsupported platform: {platform.system()}')

    ext = url.split('.')[-1]
    binary = (Path(__file__).parent / BINARY_NAME).with_suffix(f'.{ext}')

    if binary.exists():
        LOG.debug(f'{binary} already exists, skipping download.')

    else:

        msg = f'Downloading {BINARY_NAME} from {BINARY_URL}...'
        LOG.info(msg)
        print(msg)

        query_parameters = {'downloadformat': ext}
        response = requests.get(url, params=query_parameters)

        if not (response.ok and response.status_code == 200):
            raise Exception(f'Failed to download binary:  {response.reason} ({response.status_code})')

        binary.write_bytes(response.content)

        msg = f'Downloaded {binary}.'
        LOG.info(msg)
        print(msg)

    return binary
