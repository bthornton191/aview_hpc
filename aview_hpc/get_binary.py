from tempfile import gettempdir
from .version import version as PKG_VERSION
import logging
import platform
import subprocess
from pathlib import Path
import requests

REPO_URL = 'https://github.com/bthornton191/aview_hpc'

BINARY_NAME = 'aview_hpc'
BINARY_URL = {
    'windows': f'{REPO_URL}/releases/download/v{PKG_VERSION}/{BINARY_NAME}.exe',

    # Linux currently not supported
    'linux': '',
}
LOG = logging.getLogger(__name__)


def get_binary(print_=True):

    url = BINARY_URL.get(platform.system().lower())

    if not url:
        raise Exception(f'Unsupported platform: {platform.system()}')

    ext = url.split('.')[-1]
    binary = (Path(__file__).parent / BINARY_NAME).with_suffix(f'.{ext}')

    bin_version = _bin_version(binary)
    if bin_version != PKG_VERSION:
        LOG.warning(f'Binary version mismatch: {bin_version} (expected: {PKG_VERSION})')
        LOG.warning(f'Deleting {binary}...')
        binary.unlink(missing_ok=True)

    if binary.exists():
        LOG.debug(f'{binary} already exists, skipping download.')

    else:

        msg = f'Downloading {binary.name} from {url}...'
        LOG.info(msg)
        if print_:
            print(msg)

        query_parameters = {'downloadformat': ext}
        response = requests.get(url, params=query_parameters)

        if not (response.ok and response.status_code == 200):
            raise Exception(f'Failed to download binary:  {response.reason} ({response.status_code})')

        binary.write_bytes(response.content)

        msg = f'Downloaded {binary}.'
        LOG.info(msg)
        if print_:
            print(msg)

    return binary


def _bin_version(bin_file: Path):
    def load_cached(cache_file: Path):
        if cache_file.exists():
            try:
                return cache_file.read_text().strip()
            except Exception as e:
                LOG.warning(f'Failed to read cache file {cache_file}: {e}')
        return None

    def save_cache(cache_file: Path, data: str):
        try:
            cache_file.write_text(data)
        except Exception as e:
            LOG.warning(f'Failed to write cache file {cache_file}: {e}')

    cache_file = Path(gettempdir()) / (bin_file.stem + '_version.cache')
    cached_version = load_cached(cache_file)

    if cached_version and bin_file.stat().st_mtime <= cache_file.stat().st_mtime:
        version = cached_version

    else:
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            version = subprocess.check_output([str(bin_file), 'version'],
                                              text=True,
                                              startupinfo=startupinfo).strip()
        except (subprocess.CalledProcessError, OSError):
            version = None

        if version:
            save_cache(cache_file, version)

    return version
