import json
import shutil
import subprocess
import zipfile
from os import path
from pathlib import Path
from typing import Literal

import jsii
import aws_cdk as cdk

from wellapi.openapi.utils import get_openapi
from wellapi.utils import import_app

"""
https://docs.astral.sh/uv/guides/integration/aws-lambda/#deploying-a-zip-archive
"""

EXPERT_DEP_FILE = [
    "uv",
    "export",
    "--frozen",
    "--no-dev",
    "--no-editable",
    "-o",
    "requirements.txt",
]
INSTALL_DEP = [
    "uv",
    "pip",
    "install",
    "--no-installer-metadata",
    "--no-compile-bytecode",
    "--python-platform",
    "x86_64-manylinux2014",
    "--python",
    "3.12",
    "--prefix",
    "packages",
    "-r",
    "requirements.txt",
]


def install_dependencies():
    # Експорт файлу requirements.txt
    subprocess.run(EXPERT_DEP_FILE, check=True)

    # Створення директорії для інсталяції
    packages_path = Path("packages")
    packages_path.mkdir(parents=True, exist_ok=True)

    subprocess.run(INSTALL_DEP, check=True)


def copy_to_python_folder():
    src = Path("packages/lib")
    dst = Path("python/lib")
    dst.parent.mkdir(exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)


def create_zip(file_name: str, dir_name: str):
    zip_path = Path(file_name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in Path(dir_name).rglob("*"):
            if (
                str(path).startswith(".venv")
                or str(path).startswith("cdk.out")
                or "__pycache__" in str(path)
                or str(path) == file_name
            ):
                continue
            z.write(path, path.relative_to(Path(dir_name).parent))


def package_dependencies(layer_name):
    install_dependencies()
    copy_to_python_folder()
    create_zip(layer_name, "python")
    # clean up
    shutil.rmtree("packages")
    shutil.rmtree("python")


def package_app(app_name):
    create_zip(app_name, ".")


def package(layer_name: str, app_name: str):
    """
    Package the application into a zip file.
    :return:
    """
    package_app(app_name)
    package_dependencies(layer_name)


@jsii.implements(cdk.ILocalBundling)
class PackageBundling:
    def __init__(self, target: Literal["app", "dep"], zip_name: str):
        self.target = target
        self.zip_name = zip_name

    def try_bundle(self, output_dir, *, image, entrypoint=None, command=None, volumes=None, volumesFrom=None, environment=None, workingDirectory=None, user=None, local=None, outputType=None, securityOpt=None, network=None, bundlingFileAccess=None, platform=None) -> bool:
        try:
            zip_path = path.join(output_dir, self.zip_name)
            if self.target == "dep":
                package_dependencies(zip_path)
            elif self.target == "app":
                package_app(zip_path)

            return True
        except Exception as err:
            print(f"Error during bundling: {err}")
            return False


@jsii.implements(cdk.ILocalBundling)
class OpenAPIBundling:
    def __init__(
        self,
        app_srt: str,
        handlers_dir: str,
        cors: bool = False,
        role_name: str = "WellApiRole",
        openapi_file: str = "openapi.json"
    ):
        self.app_srt = app_srt
        self.handlers_dir = handlers_dir
        self.cors = cors
        self.role_name = role_name
        self.openapi_file = openapi_file

    def try_bundle(self, output_dir, *, image, entrypoint=None, command=None, volumes=None, volumesFrom=None, environment=None, workingDirectory=None, user=None, local=None, outputType=None, securityOpt=None, network=None, bundlingFileAccess=None, platform=None) -> bool:
        try:
            app = import_app(self.app_srt, self.handlers_dir)

            resp = get_openapi(
                title=app.title,
                version=app.version,
                openapi_version="3.0.1",
                description=app.description,
                lambdas=app.lambdas,
                tags=app.openapi_tags,
                servers=app.servers,
                cors=self.cors,
                role_name=self.role_name,
            )

            with open(path.join(output_dir, self.openapi_file), "w") as f:
                json.dump(resp, f)

            return True
        except Exception as err:
            print(f"Error during bundling: {err}")
            return False
