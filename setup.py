from setuptools import setup
from distutils.spawn import find_executable
import platform


def readme():
    with open("README.md") as f:
        return f.read()


dependencies = ["pyperclip", "click", "appdirs", "daemonize"]
if find_executable("fswatch") is None:
    if platform.system() == "Linux":
        dependencies.append("inotify")
    else:
        raise ValueError(
            "inkscape-figures needs fswatch to run on MacOS. You "
            "can install it using `brew install fswatch`"
        )

setup(
    name="pdftex-figures",
    version="1.0.7",
    description="Script for managing figures",
    long_description=readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/marromlam/inkscape-figures",
    author="Marcos Romero Lamas",
    author_email="gilles@castel.dev",
    license="MIT",
    classifiers=[
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
    ],
    packages=["pdftexfigures"],
    scripts=["bin/pdftex-figures"],
    install_requires=dependencies,
    include_package_data=True,
)
