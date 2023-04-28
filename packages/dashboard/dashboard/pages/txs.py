import dash
from dash import html, dcc, Input, Output, callback
from dashboard.index import API_URL
import requests
import pandas as pd
import plotly.express as px, plotly.graph_objects as go

dash.register_page(__name__, path="/")
print(f"{API_URL}/txs/daily")
daily = requests.get(f"{API_URL}/txs/daily").json()

daily_df = pd.DataFrame(daily)

cum = requests.get(f"{API_URL}/txs/cumulative").json()

cum_df = pd.DataFrame(cum)

layout = html.Div(
    children=[
        dcc.Graph(
            figure=px.line(daily_df, x="day", y="tx_count", title="Daily Transactions")
        ),
        dcc.Graph(
            figure=px.line(
                cum_df,
                x="day",
                y="tx_count",
                title="Cumulative Transactions",
            )
        ),
    ]
)
