import json

import click

from wellapi import WellApi
from wellapi.local.server import run_local_server
from wellapi.openapi.utils import get_openapi
from wellapi.utils import import_app, load_handlers

# ruff: noqa: W291
WELLAPI_ACII = """
 _     _  _______  ___      ___        _______  _______  ___  
| | _ | ||       ||   |    |   |      |   _   ||       ||   | 
| || || ||    ___||   |    |   |      |  |_|  ||    _  ||   | 
|       ||   |___ |   |    |   |      |       ||   |_| ||   | 
|       ||    ___||   |___ |   |___   |       ||    ___||   | 
|   _   ||   |___ |       ||       |  |   _   ||   |    |   | 
|__| |__||_______||_______||_______|  |__| |__||___|    |___| 
"""


@click.group()
def cli():
    click.echo(click.style(WELLAPI_ACII, fg="magenta"))


@cli.command()
@click.argument("app_srt", default="main:app")
@click.argument(
    "handlers_dir", default="handlers", type=click.Path(exists=True, resolve_path=True)
)
def openapi(app_srt: str, handlers_dir: str):
    app: WellApi = import_app(app_srt)
    load_handlers(handlers_dir)

    resp = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version="3.0.1",
        description=app.description,
        lambdas=app.lambdas,
        tags=app.openapi_tags,
        servers=app.servers,
        cors=False,
    )

    with open("openapi.json", "w") as f:
        json.dump(resp, f)


@cli.command()
@click.argument("app_srt", default="main:app")
@click.argument(
    "handlers_dir", default="handlers", type=click.Path(exists=True, resolve_path=True)
)
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8000, type=click.INT)
@click.option(
    "--autoreload/--no-autoreload",
    default=True,
    help="Automatically restart server when code changes.",
)
def run(app_srt: str, handlers_dir: str, host="127.0.0.1", port=8000, autoreload=True):
    run_local_server(app_srt, handlers_dir, host, port, autoreload)


if __name__ == "__main__":
    cli()
