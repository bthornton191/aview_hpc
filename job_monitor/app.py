"""
Notes
-----
* Anything that is used as an input to a callback must be in this
"""
import logging
import re
import sys
from logging.handlers import WatchedFileHandler
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import Dash

from job_monitor import APP_NAME

from .components.error_modal import ERROR_MODAL
from .components.header import HEADER
from .components.job_details_modal import JOB_DETAILS_MODAL
from .components.job_table import JOB_TABLE
from .components.job_table import LOAD_BUTTON_ROW

DBC_CSS = 'https://cdn.jsdelivr.net/gh/AnnMarieW/dash-bootstrap-templates/dbc.min.css'
JS_FILE = Path(__file__).parent / 'assets' / 'filters.js'

APP = Dash(re.sub(r'\s+', '_', APP_NAME).lower(),
           external_stylesheets=[dbc.themes.SPACELAB, dbc.icons.FONT_AWESOME, DBC_CSS],
           suppress_callback_exceptions=True,
           
           title=APP_NAME)

# SERVER = APP.server
APP.layout = dbc.Container(
    children=[HEADER,
              LOAD_BUTTON_ROW,
              JOB_TABLE,
              JOB_DETAILS_MODAL,
              ERROR_MODAL],
    fluid=True,
    className="dbc dbc-ag-grid",
    style={'height': '90vh'})


def main():
    # Set up logging to file
    handler = WatchedFileHandler(re.sub(r'\s+', '_', APP_NAME).lower() + '.log')
    formatter = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s:%(message)s', '%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel('INFO')
    # Remove existing handlers for this file name, if any
    for old_handler in [h for h in root.handlers if (isinstance(h, WatchedFileHandler)
                                                     and h.baseFilename == handler.baseFilename)]:
        root.handlers.remove(old_handler)
    root.addHandler(handler)

    APP.run_server(debug=len(sys.argv) > 1 and sys.argv[1] == '--debug',
                   host='localhost',
                   port=8080)


if __name__ == '__main__':
    main()
