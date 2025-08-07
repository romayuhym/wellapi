import json
from typing import Literal

import click

from wellapi import WellApi
from wellapi.build.packager import package_app, package_dependencies
from wellapi.openapi.utils import get_openapi
from wellapi.utils import import_app

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
@click.option(
    "--output", type=click.STRING
)
@click.option("--cors", default=False, type=click.BOOL, help="Enable CORS for the API")
@click.option(
    "--role_name", default="WellApiRole", type=click.STRING, help="IAM role name for the API"
)
def openapi(
    app_srt: str, handlers_dir: str, output: str, cors: bool = False, role_name: str = "WellApiRole"
):
    app: WellApi = import_app(app_srt, handlers_dir)

    resp = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version="3.0.1",
        description=app.description,
        lambdas=app.lambdas,
        tags=app.openapi_tags,
        servers=app.servers,
        cors=cors,
        role_name=role_name,
    )

    with open(output, "w") as f:
        json.dump(resp, f)


@cli.command()
@click.argument(
    "target", type=click.Choice(['app', 'dep'])
)
@click.argument(
    "zip_name", type=click.STRING
)
def build(target: Literal["app", "dep"], zip_name: str):
    if target == "dep":
        package_dependencies(zip_name)
    elif target == "app":
        package_app(zip_name)
    else:
        raise click.BadParameter("Invalid target. Use 'app' or 'dep'.")


if __name__ == "__main__":
    cli()
