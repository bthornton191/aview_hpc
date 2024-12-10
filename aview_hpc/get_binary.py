from .version import version as PKG_VERSION
import logging
import platform
import subprocess
from pathlib import Path
import requests

REPO_URL = 'https://github.com/bthornton191/aview_hpc'

BINARY_NAME = 'aview_hpc'
BINARY_URL = {
    'windows': f'{REPO_URL}/releases/tag/v{PKG_VERSION}/download/{BINARY_NAME}.exe',

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

    if binary.exists() and _bin_version(binary) == PKG_VERSION:
        LOG.debug(f'{binary} already exists, skipping download.')

    else:

        binary.unlink(missing_ok=True)
        msg = f'Downloading {binary.name} from {url}...'
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


def _bin_version(bin_file: Path):
    try:
        version = subprocess.check_output([str(bin_file), 'version'], text=True).strip()
    except subprocess.CalledProcessError:
        version = None

    return version
