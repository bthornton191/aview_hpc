
import dash_bootstrap_components as dbc
from dash import html

SAVVYANALYST_LOGO = ('https://raw.githubusercontent.com/bthornton191/gifs/0dfb5f847db7255d06cb50700de67a0634306fd5/savvyanalyst_transparent.png')

"""
Includes the color switch, title, and logo.
"""
HEADER = dbc.Stack([
    html.Span([dbc.Label(className="fa fa-moon", html_for="switch"),
               dbc.Switch(id="switch",
                          value=True,
                          className="d-inline-block ms-1",
                          persistence=True),
               dbc.Label(className="fa fa-sun", html_for="switch")]),
    html.Div(html.H1(children='Job Monitor', style={'textAlign': 'center'}),
             className='mx-auto'),
    html.A(html.Img(src=SAVVYANALYST_LOGO,
                    style={'height': '50px', 'align': 'left'}),
           href='https://github.com/bthornton191')],
    direction='horizontal')
