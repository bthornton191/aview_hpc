import time
import traceback as tb

import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, html, no_update
from dash.dcc import Interval

from aview_hpc._cli import get_job_table

from .bulk_download_button import BULK_DOWNLOAD_BUTTON, BULK_DOWNLOAD_PROGRESS_BAR

DEFAULT_COL_DEF = {'flex': 1, 'minWidth': 50, 'sortable': True, 'resizable': True,
                   'filter': True, 'cellRenderer': 'agAnimateShowChangeCellRenderer', }

COL_DEF = {'cellRenderer': 'agAnimateShowChangeCellRenderer',
           'minWidth': 50}


def get_column_def(col: str):
    if col.lower() in ['start', 'end']:
        col_def = {'filter': 'agDateColumnFilter',
                   "filterValueGetter": {"function": "d3.isoParse()(params.data.date)"},
                   "filterParams": {
                       "browserDatePicker": True,
                       "minValidYear": time.localtime().tm_year-10,
                       "maxValidYear": time.localtime().tm_year,
                   }, }

    elif col.lower() in ['state']:
        col_def = {'cellStyle': {
            'styleConditions': [
                {'condition': 'params.value == "RUNNING"',
                 'style': {'color': 'lightgreen'}},
                {'condition': 'params.value == "PENDING"',
                 'style': {'color': 'lightyellow'}},
                {'condition': 'params.value == "COMPLETED"',
                 'style': {'color': 'lightblue'}},
                {'condition': 'params.value == "TIMEOUT"',
                 'style': {'color': 'lightcoral'}},
                {'condition': 'params.value.includes("CANCELLED")',
                 'style': {'color': 'lightgray'}},
                {'condition': 'params.value.includes("FAILED")',
                 'style': {'color': 'orange'}},
            ]}}

    elif col.lower() in ['ncpus']:
        col_def = {
            'valueParser': 'Number(params.newValue)',
            'aggFunc': 'sum',
        }

    elif col.lower() in ['jobid']:
        col_def = {'sort': 'desc'}

    else:
        col_def = {}

    return {'field': col, **col_def, **COL_DEF}


INIT_JOB_TABLE = get_job_table()
JOB_TABLE = dag.AgGrid(
    id='table',
    rowData=INIT_JOB_TABLE.to_dict('records'),
    columnDefs=[get_column_def(col) for col in INIT_JOB_TABLE.columns],
    dashGridOptions={
        'rowSelection': 'multiple',
        'enableCellTextSelection': True,
        'suppressAggFuncInHeader': False,
    },
    style={'height': '100%'},
    columnSize='autoSize',
    defaultColDef=DEFAULT_COL_DEF,
)


@callback(Output('table', 'rowData'),
          Output('last_refresh', 'children'),
          Output('refresh-badge', 'children'),
          Output('error-text', 'children'),
          Output('load-button-icon', 'children'),
          Input('load-button', 'n_clicks'),
          Input('interval-component', 'n_intervals'),
          State('modal', 'is_open'))
def update_data(n, interval, modal_open):
    """This callback Updates the data in the table. Triggered by the load button and the timer.

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
        return no_update, no_update, no_update, no_update, no_update

    try:
        job_table = get_job_table().to_dict('records')
        t_str = f'Last Refresh: {time.strftime("%Y-%m-%d %I:%M:%S %p", time.localtime())}'
    except Exception:
        return (no_update,
                no_update,
                ['!'],
                [html.Pre(tb.format_exc())],
                no_update)

    return job_table, t_str, [], [], no_update


LOAD_BUTTON = dbc.Button([
    dbc.Spinner(html.I(id='load-button-icon',
                       className='fa-solid fa-arrows-rotate'),
                size='sm'),
    dbc.Badge(id='refresh-badge',
              color='danger',
              pill=True,
              href='#',
              text_color='white',
              className='position-absolute top-0 start-100 translate-middle')],
    id='load-button',
    n_clicks=0,
    className='position-relative')


LAST_REFRESH = html.Div(
    dbc.Row([
        dbc.Col(
            html.Div(id='last_refresh',
                     style={'text-align': 'right'}
                     )
        )
    ]),
)


LOAD_BUTTON_ROW = html.Div(
    dbc.Row([
        dbc.Col(dbc.Stack([LOAD_BUTTON, BULK_DOWNLOAD_BUTTON, BULK_DOWNLOAD_PROGRESS_BAR],
                          direction='horizontal',
                          gap=2)),
        dbc.Col(Interval(id='interval-component',
                         interval=60000,  # Refresh every 60 seconds
                         n_intervals=0)),
        dbc.Col(LAST_REFRESH, width=2, style={'justify-content': 'right'}),
    ]))
