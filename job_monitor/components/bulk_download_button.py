
import base64
import logging
from io import BytesIO
from pathlib import Path
from typing import Callable, List

import dash_bootstrap_components as dbc
import diskcache
from dash import Input, Output, State, callback, html, no_update
from dash.dcc import Download
from dash.long_callback import DiskcacheLongCallbackManager

from aview_hpc._cli import RES_EXTS, hpc_session

LOG = logging.getLogger(__name__)
CACHE = diskcache.Cache("./cache")
LONG_CALLBACK_MANAGER = DiskcacheLongCallbackManager(CACHE)

BULK_DOWNLOAD_BUTTON = html.Div([
    dbc.Button(
        dbc.Spinner(
            html.I(id='bulk-download-button-icon',
                   className='fa-solid fa-download'),
            id='bulk-download-button-spinner',
            size='sm'),
        id='bulk-download-button',
        className='btn btn-primary',  # Add the bootstrap button class here
        style={'display': 'none'},
    ),
    Download(id='bulk-download', base64=True)])

BULK_DOWNLOAD_PROGRESS_BAR = html.Progress(
    id='bulk-download-progress-bar',
    max=100,
    value='100',
    style={'display': 'none'})


@callback(
    Output('bulk-download-button', 'style'),
    Input('table', 'selectedRows'),
    Input('bulk-download-button-icon', 'data-dash-is-loading'),
    State('bulk-download-button', 'style'),)
def show_download_button(row_data: List[dict], loading_state, style: dict):
    if row_data or loading_state:
        style['display'] = 'block'
    else:
        style['display'] = 'none'
    return style


@callback(
    Output('bulk-download-button-icon', 'children'),
    Output('bulk-download', 'data'),
    Input('bulk-download-button', 'n_clicks'),
    State('table', 'selectedRows'),
    progress=[Output('bulk-download-progress-bar', 'value')],
    progress_default=['100'],
    background=True,
    manager=LONG_CALLBACK_MANAGER,
    prevent_initial_call=True,
)
def bulk_download(set_progress: Callable, n: int, row_data: List[dict]):
    if n == 0 or not row_data:
        return [], no_update

    inc = 50/(len(row_data)*len(RES_EXTS))
    progress = 0
    set_progress(str(progress))

    with hpc_session() as hpc:
        _, stdout, _ = hpc.ssh.exec_command('mktemp -d')
        remote_zip = (Path(stdout.read().decode().strip()) / 'tmp.zip').as_posix()

        LOG.info(f'Creating zip file {remote_zip}')
        for row in row_data:
            remote_dir = Path(row['WorkDir'])
            job_name = Path(row['JobName'])

            fids = [BytesIO() for _ in RES_EXTS]
            for fid, ext in zip(fids, RES_EXTS):

                remote_file = ((remote_dir / job_name).with_suffix(ext)).as_posix()

                # Check if the file exists...
                if (Path(remote_file).name in hpc.ftp.listdir(remote_dir.as_posix())):
                    # If it does, add it to the zip
                    _, stdout, stderr = hpc.ssh.exec_command(f'zip -j {remote_zip} {remote_file}')

                    if stderr.read().decode() or 'warning' in stdout.read().decode().lower():
                        LOG.error(f'Error adding {remote_file} to {remote_zip}')
                    else:
                        LOG.info(f' Added {remote_file} to {remote_zip}')

                progress += inc
                set_progress(str(progress))

        def update_progress(transferred: int, total: int):
            set_progress(str(int(progress + transferred / total * 50)))

        LOG.info(f'Downloading {remote_zip}')
        fid = BytesIO()
        hpc.ftp.getfo(remote_zip, fid, callback=update_progress)

    return [], {'content': base64.b64encode(fid.getvalue()).decode(),
                'filename': Path(job_name).with_suffix('.zip').name}


def add_remote_file_to_zip(remote_file: Path, remote_zip: Path):
    with hpc_session() as hpc:
        hpc.ssh.exec_command(f'zip -r {remote_zip} {remote_file}')


@callback(Output('bulk-download-progress-bar', 'style'),
          Input('bulk-download-progress-bar', 'value'))
def show_progress_bar(value):
    if float(value) == 100:
        return {'display': 'none'}
    else:
        return {'display': 'block'}
