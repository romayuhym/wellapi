import shutil
import subprocess
import zipfile
from pathlib import Path

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
