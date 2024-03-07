"""
Notes
-----
* Anything that is used as an input to a callback must be in this
"""
import logging
import re
import time
from io import BytesIO
from logging.handlers import WatchedFileHandler
from pathlib import Path
from typing import List

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, State, callback, clientside_callback
from dash import html, no_update
from dash.dcc import Download, Interval, Loading, Textarea

from aview_hpc._cli import RES_EXTS, get_job_messages, get_job_table
from aview_hpc._cli import hpc_session

from .components.footer import FOOTER
from .components.header import HEADER

APP_NAME = 'slurmmonitor'
DBC_CSS = 'https://cdn.jsdelivr.net/gh/AnnMarieW/dash-bootstrap-templates/dbc.min.css'


# Define the path to your local JavaScript library
JS_FILE = Path(__file__).parent / 'assets' / 'filters.js'


def setup_logging():
    """Setup logging to file and console."""
    # Set up logging to file
    handler = WatchedFileHandler(f'{APP_NAME}.log')
    formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s:%(message)s', '%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel('DEBUG')
    # Remove existing handlers for this file name, if any
    for old_handler in [h for h in root.handlers if (isinstance(h, WatchedFileHandler)
                                                     and h.baseFilename == handler.baseFilename)]:
        root.handlers.remove(old_handler)
    root.addHandler(handler)
    return logging.getLogger(__name__)


HOST_INPUT = html.Div(
    dbc.Row([
        dbc.Col(dbc.Button(html.I(className='fa-solid fa-arrows-rotate'), id='load-button', n_clicks=0)),
        dbc.Col(Interval(id='interval-component',
                         interval=60000,  # Refresh every 60 seconds
                         n_intervals=0)),
        dbc.Col(FOOTER),
    ]))


# date_obj = 'd3.timeParse("%Y-%m-%dT%H:%M:%S %p")(params.data.date)'

JOB_TABLE = dag.AgGrid(
    id='table',
    rowData=get_job_table().to_dict('records'),
    columnDefs=[{'field': col} if col.lower() not in ['start', 'end'] else
                {'field': col,
                #  'valueGetter': {'function': 'params.data.date'},
                 'filter': 'agDateColumnFilter',
                 'filterParams': {'comparator': {'function': 'DateComparator'}},
                 #  'valueFormatter': {'function': f'd3.timeFormat("%Y-%m-%dT%H:%M:%S")({date_obj})'}
                 }
                for col in get_job_table().columns],
    dashGridOptions={'rowSelection': 'single', 'enableCellTextSelection': True},
    style={'height': '100%'},
    columnSize='responsiveSizeToFit',
    defaultColDef={'flex': 1, 'minWidth': 50, 'sortable': True, 'resizable': True,
                   'filter': True, 'cellRenderer': 'agAnimateShowChangeCellRenderer', },
)


MODAL = dbc.Modal(dbc.Col(
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

APP = Dash(__name__,
           external_stylesheets=[dbc.themes.SPACELAB, dbc.icons.FONT_AWESOME, DBC_CSS],
           suppress_callback_exceptions=True,
           title='Job Monitor')
SERVER = APP.server
APP.layout = dbc.Container(
    children=[HEADER,
              HOST_INPUT,
              JOB_TABLE,
              MODAL],
    fluid=True,
    className="dbc dbc-ag-grid",
    style={'height': '90vh'})


# --------------------------------------------------------------------------------------------------
# Callbacks
# --------------------------------------------------------------------------------------------------
# This callback is used to update the data in the table. It is triggered by the load button, the
# profile url input, and a timer.
@callback(Output('table', 'rowData'),
          Output('last_refresh', 'children'),
          Input('load-button', 'n_clicks'),
          Input('interval-component', 'n_intervals'),
          State('modal', 'is_open'))
def update_data(n, interval, modal_open):
    """Updates the data in the table. Triggered by the load button, the profile url input, and a timer.
    If the url is empty, the table will be empty. If the url is not empty, the table will be populated with
    the data from the url. The url is expected to be a mountain project tick export url. 

    Parameters
    ----------
    n : int
        Number of times the load button has been clicked
    interval : int
        Number of times the timer has triggered
    modal_open : bool
        Whether the modal is open

    Returns
    -------
    DataFrame
        The data to be displayed in the table
    str
        The last refresh time
    """
    if modal_open:
        return no_update, no_update

    t_str = time.strftime("%Y-%m-%d %I:%M:%S %p", time.localtime())

    return get_job_table().to_dict('records'), f'Last Refresh: {t_str}'


# This is a callback that runs when a cell in the table is clicked. It will open a model with
# additional information about the job.
@callback(Output('modal', 'is_open'),
          Output('modal-body', 'children'),
          Output('modal-footer', 'children'),
          Input('table', 'cellClicked'),
          Input('table', 'selectedRows'),
          prevent_initial_call=True)
def open_modal(cellClicked: dict, selectedRows: List[dict]):
    """Opens a modal with additional information about the job.

    Parameters
    ----------
    cellClicked : dict
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
        # style={'height': '100px'},
        columnSize='autoSize',
        defaultColDef={'flex': 1, 'minWidth': 20, 'resizable': True, },
        dashGridOptions={'rowSelection': 'single', 'enableCellTextSelection': True},
        style={'height': '75vh', 'headerHeight': '0'},
    )

    msg_viewer = Textarea(id='msg-viewer',
                          readOnly=True,
                          wrap='off',
                          loading_state={'is_loading': True},
                          style={'width': '100%', 'height': '75vh', 'font-family': 'monospace', 'resize': 'none'})

    spinner = Loading(id='msg-viewer-spinner', type='default', children=[msg_viewer])

    download = Loading(id='download-spinner',
                       type='default',
                       children=[dbc.Button('Download', id='download-button', n_clicks=0),
                                 Download(id='res-download'),
                                 Download(id='req-download'),
                                 Download(id='gra-download'),
                                 Download(id='msg-download'),
                                 Download(id='out-download')])
    return True, [dbc.Row([dbc.Col(table, width=4), dbc.Col(spinner, width=8)])], [download]


# This callback is used to download the job results
@callback(Output('res-download', 'data'),
          Output('req-download', 'data'),
          Output('gra-download', 'data'),
          Output('msg-download', 'data'),
          Output('out-download', 'data'),
          Input('download-button', 'n_clicks'),
          State('details-table', 'rowData'))
def download_results(n, row_data):
    if n == 0:
        return no_update, no_update, no_update, no_update, no_update
    remote_dir = Path(next(d['value'] for d in row_data if d['name'] == 'WorkDir'))
    job_name = str(next(d['value'] for d in row_data if d['name'] == 'JobName'))

    with hpc_session(remote_dir=remote_dir) as hpc:
        fids = [BytesIO() for _ in RES_EXTS]
        for fid, ext in zip(fids, RES_EXTS):
            try:
                hpc.ftp.getfo((remote_dir / job_name).with_suffix(ext).as_posix(), fid)
            except FileNotFoundError:
                fid.write(b'')

    return [{'content': fid.getvalue().decode(), 'filename': f'{job_name}{ext}'}
            for fid, ext in zip(fids, RES_EXTS)]


@callback(Output('msg-viewer', 'value'),
          Output('msg-viewer', 'loading_state'),
          Input('modal-body', 'children'),
          Input('details-table', 'rowData'),
          Input('msg-viewer', 'loading_state'))
def populate_msg(_, row_data, loading_state: dict):
    remote_dir = Path(next(d['value'] for d in row_data if d['name'] == 'WorkDir'))
    msg = get_job_messages(remote_dir)
    loading_state['is_loading'] = False
    return msg, loading_state


# --------------------------------------------------------------------------------------------------
# This callback is used to change from light mode to dark mode. It is triggered by the switch.
# --------------------------------------------------------------------------------------------------
clientside_callback(
    """
    (switchOn) => {
       switchOn
         ? document.documentElement.setAttribute('data-bs-theme', 'light')
         : document.documentElement.setAttribute('data-bs-theme', 'dark')
       return window.dash_clientside.no_update
    }
    """,
    Output("switch", "id"),
    Input("switch", "value"),
)


def camel_to_title(s: str) -> str:
    """Converts a camel case string to title case.

    Parameters
    ----------
    s : str
        The string to convert

    Returns
    -------
    str
        The converted string
    """
    return re.sub(r'(?<!^)(?=[A-Z])', ' ', s).title()


def main():
    APP.run_server(debug=False, host='localhost', port=8080)


if __name__ == '__main__':
    main()
