import time

import dash_bootstrap_components as dbc
from dash import html

FOOTER = html.Div(dbc.Row([
    dbc.Col(
        html.Div(
            id='last_refresh',
            children=f'Last Refresh: {time.strftime("%Y-%m-%d %I:%M:%S %p", time.localtime())}',
            style={'textAlign': 'right'},
        ),
    ),
]),
    style={'margin': '10px', })


