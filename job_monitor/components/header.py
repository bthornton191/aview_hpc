import dash_bootstrap_components as dbc
from dash import Input, Output, clientside_callback, html

from .. import APP_NAME

SAVVYANALYST_LOGO = '/'.join([
    'https://raw.githubusercontent.com',
    'bthornton191',
    'gifs',
    '0dfb5f847db7255d06cb50700de67a0634306fd5',
    'savvyanalyst_transparent.png',
])


"""
Includes the color switch, title, and logo.
"""
LIGHT_DARK_SWITCH = html.Span([
    dbc.Label(className="fa fa-moon", html_for="switch"),
    dbc.Switch(id="switch",
               value=True,
               className="d-inline-block ms-1",
               persistence=True),
    dbc.Label(className="fa fa-sun", html_for="switch"),
])

HEADER = dbc.Stack([
    LIGHT_DARK_SWITCH,
    html.Div(html.H1(children=APP_NAME, style={'textAlign': 'center'}), className='mx-auto'),
    html.A(html.Img(src=SAVVYANALYST_LOGO, style={'height': '50px', 'align': 'left'}),
           href='https://github.com/bthornton191')
],
    direction='horizontal')

"""
This callback is used to change from light mode to dark mode. It is triggered by the switch.
"""
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
