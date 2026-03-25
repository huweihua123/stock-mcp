"""Thin application entrypoint."""

from src.server.transports.http.app import create_http_app

create_app = create_http_app
app = create_http_app()
