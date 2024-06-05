from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Callable, List

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import diskcache
from dash import Input, Output, State, callback, no_update, html
from dash.dcc import Download, Loading, Textarea, Markdown
from dash.long_callback import DiskcacheLongCallbackManager

from aview_hpc._cli import RES_EXTS, get_job_messages, get_last_update
from aview_hpc._cli import hpc_session

CACHE = diskcache.Cache("./cache")
LONG_CALLBACK_MANAGER = DiskcacheLongCallbackManager(CACHE)

JOB_DETAILS_MODAL = dbc.Modal(dbc.Col(
    [
        dbc.Row(dbc.ModalHeader('Job Details', style={'textAlign': 'center'}), align='center', justify='center'),
        dbc.Row(dbc.ModalBody(id='modal-body')),
        dbc.Row(dbc.ModalFooter(id='modal-footer')),
    ]),
    id='modal',
    is_open=False,
    size='xl',
    centered=True,
    className="dbc dbc-ag-grid",
)


DOWNLOAD_PROGRESS_BAR = html.Progress(
    id='download-progress-bar',
    max=100,
    value='100',
    style={'display': 'none'})


@callback(Output('modal', 'is_open'),
          Output('modal-body', 'children'),
          Output('modal-footer', 'children'),
          Input('table', 'cellDoubleClicked'),
          State('table', 'selectedRows'),
          prevent_initial_call=True)
def open_modal(cellDoubleClicked: dict, selectedRows: List[dict]):
    """This is a callback that runs when a cell in the table is clicked. It will open a model with
    additional information about the job.

    Parameters
    ----------
    cellDoubleClicked : dict
        The row that was clicked
    selectedRows : List[dict]
        The rows that are selected
    n : int
        Number of times the close button has been clicked

    Returns
    -------
    bool
        Whether the modal is open
    list
        The children of the modal
    """
    if not selectedRows:
        return False, [], []

    info = selectedRows[0]

    table = dag.AgGrid(
        id='details-table',
        rowData=[{'name': key, 'value': str(value)} for key, value in info.items()],
        columnDefs=[{'field': col, 'tooltipField': col} for col in ['name', 'value']],
        columnSize='autoSize',
        defaultColDef={'flex': 1, 'minWidth': 20, 'resizable': True, },
        dashGridOptions={'rowSelection': 'single', 'enableCellTextSelection': True},
        style={'height': '70vh', 'headerHeight': '0'},
    )

    msg_viewer = Markdown(id='msg-viewer',
                          #   readOnly=True,
                          #   wrap='off',
                          loading_state={'is_loading': True},
                          style={'width': '100%',
                                 'height': '70vh',
                                 'overflow': 'scroll',
                                 #  'font-family': 'monospace',
                                 'resize': 'none'})

    download = Loading([dbc.Button('Download', id='download-button', n_clicks=0),
                        Download(id='res-download'),
                        Download(id='req-download'),
                        Download(id='gra-download'),
                        Download(id='msg-download'),
                        Download(id='out-download')])
    modal_body = [dbc.Row([dbc.Col(table, width=4), dbc.Col(Loading(msg_viewer), width=8)]),
                  dbc.Row(Loading(dbc.Col(id='last-update-timestamp')), justify='center')]
    return True, modal_body, [DOWNLOAD_PROGRESS_BAR, download]


@callback(Output('msg-viewer', 'children'),
          Output('msg-viewer', 'loading_state'),
          Input('modal-body', 'children'),
          Input('details-table', 'rowData'),
          Input('msg-viewer', 'loading_state'))
def populate_msg(_, row_data, loading_state: dict):
    remote_dir = Path(next(d['value'] for d in row_data if d['name'] == 'WorkDir'))
    msg = get_job_messages(remote_dir)
    loading_state['is_loading'] = False
    return f'```adams_msg\n{msg}\n```', loading_state


@callback(Output('last-update-timestamp', 'children'),
          Input('details-table', 'rowData'))
def update_timestamp(row_data):
    remote_dir = Path(next(d['value'] for d in row_data if d['name'] == 'WorkDir'))
    last_time, last_file = get_last_update(remote_dir)
    time_since = (datetime.now() - last_time)
    time_since_str = []
    if time_since.days:
        time_since_str.append(f'{time_since.days} days')
    if time_since.seconds // 3600:
        time_since_str.append(f'{time_since.seconds // 3600} hours')
    if time_since.seconds % 3600 // 60:
        time_since_str.append(f'{time_since.seconds % 3600 // 60} minutes')
    if time_since.seconds % 3600 % 60:
        time_since_str.append(f'{time_since.seconds % 3600 % 60} seconds')

    return dbc.Col([dbc.Row(f'      Last Update: {last_time} - ({" ".join(time_since_str)} ago)'),
                    dbc.Row(f'      Last File: {last_file.name}')],
                   style={'margin': '10px'})


@callback(Output('res-download', 'data'),
          Output('req-download', 'data'),
          Output('gra-download', 'data'),
          Output('msg-download', 'data'),
          Output('out-download', 'data'),
          Input('download-button', 'n_clicks'),
          State('details-table', 'rowData'),
          progress=[Output('download-progress-bar', 'value')],
          progress_default=['100'],
          background=True,
          manager=LONG_CALLBACK_MANAGER,)
def download_results(set_progress: Callable, n, row_data):
    """This callback is used to download the job results"""
    if n == 0:
        return no_update, no_update, no_update, no_update, no_update
    remote_dir = Path(next(d['value'] for d in row_data if d['name'] == 'WorkDir'))
    job_name = str(next(d['value'] for d in row_data if d['name'] == 'JobName'))

    inc = 100/len(RES_EXTS)
    progress = 0
    set_progress(str(progress))

    def update_progress(transferred: int, total: int):
        set_progress(str(int(progress + transferred / total * inc)))

    with hpc_session(remote_dir=remote_dir) as hpc:
        fids = [BytesIO() for _ in RES_EXTS]
        for fid, ext in zip(fids, RES_EXTS):
            try:
                hpc.ftp.getfo((remote_dir / job_name).with_suffix(ext).as_posix(),
                              fid,
                              callback=update_progress)
            except FileNotFoundError:
                fid.write(b'')

            progress += inc
            set_progress(str(progress))

    return [{'content': fid.getvalue().decode(), 'filename': Path(job_name).with_suffix(ext).name}
            for fid, ext in zip(fids, RES_EXTS)]


@callback(Output('download-progress-bar', 'style'),
          Input('download-progress-bar', 'value'))
def show_progress_bar(value):
    if float(value) == 100:
        return {'display': 'none'}
    else:
        return {'display': 'block'}
