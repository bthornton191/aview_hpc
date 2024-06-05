import dash_bootstrap_components as dbc
from dash import Input, Output, callback, no_update

ERROR_MODAL = dbc.Modal(dbc.Col(
    [
        dbc.Row(dbc.ModalHeader('Error', style={'textAlign': 'center'}), align='center', justify='center'),
        dbc.Row(dbc.ModalBody(id='error-text'), style={'font-family': 'monospace'}),
    ]),
    id='error-modal',
    is_open=False,
    size='xl',
    centered=True,
)


@callback(Output('error-modal', 'is_open'),
          Input('refresh-badge', 'n_clicks'))
def open_error(n):
    """This callback is used to open the error modal

    Parameters
    ----------
    n : int
        Number of times the refresh badge has been clicked

    Returns
    -------
    bool
        Whether the modal should open
    """
    return True if n else no_update
