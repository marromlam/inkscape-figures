#!/usr/bin/env python3

import os
import re
import logging
import subprocess
import warnings
from pathlib import Path
from shutil import copy
from daemonize import Daemonize
import click
import platform
from .picker import pick
import pyperclip
from appdirs import user_config_dir

from collections import defaultdict
from time import time, sleep
from threading import Timer

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
log = logging.getLogger("pdftex-figures")


def inkscape(path: str | Path) -> None:
    with warnings.catch_warnings():
        # leaving a subprocess running after interpreter exit raises a
        # warning in Python3.7+
        warnings.simplefilter("ignore", ResourceWarning)
        _ = subprocess.Popen(["inkscape", str(path)])


def indent(text: str, indentation: int = 0) -> str:
    lines = text.split("\n")
    return "\n".join(" " * indentation + line for line in lines)


def beautify(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").title()


def latex_template(name, title):
    return "\n".join(
        (
            r"\begin{figure}[ht]",
            r"    \centering",
            rf"    \incfig{{{name}}}",
            rf"    \caption{{{title}}}",
            rf"    \label{{fig:{name}}}",
            r"\end{figure}",
        )
    )


# From https://stackoverflow.com/a/67692
def import_file(name: str, path: str | Path):
    import importlib.util as util

    spec = util.spec_from_file_location(name, path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load user config

user_dir = Path("/Users/marcos/.config/pdftex-figures")

if not user_dir.is_dir():
    user_dir.mkdir()

roots_file = user_dir / "roots"
template = user_dir / "template.svg"
config = user_dir / "config.py"

if not roots_file.is_file():
    roots_file.touch()

if not template.is_file():
    source = str(Path(__file__).parent / "template.svg")
    destination = str(template)
    _ = copy(source, destination)

if config.exists():
    config_module = import_file("config", config)
    latex_template = config_module.latex_template


def add_root(path):
    path = str(path)
    roots = get_roots()
    if path in roots:
        return None

    roots.append(path)
    roots_file.write_text("\n".join(roots))


def get_roots():
    current_dir = os.getcwd()
    ans = [root for root in roots_file.read_text().split("\n") if root != ""]
    if current_dir not in ans:
        ans.append(current_dir)
    return ans


@click.group()
def cli():
    pass


@cli.command()
@click.option("--daemon/--no-daemon", default=True)
def watch(daemon: bool) -> None:
    """
    Watches for figures.
    """
    if platform.system() == "Linux":
        watcher_cmd = watch_daemon_inotify
    else:
        watcher_cmd = watch_daemon_fswatch

    if daemon:
        daemon = Daemonize(
            app="pdftex-figures",
            pid="/tmp/pdftex-figures.pid",
            action=watcher_cmd,
        )
        daemon.start()
        log.info("Watching figures.")
    else:
        log.info("Watching figures.")
        watcher_cmd()


def convert_svg_to_pdf_tex(filepath: Path) -> None:
    # A file has changed
    if filepath.suffix != ".svg":
        log.debug(f"File has changed, but is nog an svg {filepath.suffix}")
        return

    log.info("Recompiling %s", filepath)

    pdf_path = filepath.parent / (filepath.stem + ".pdf")
    name = filepath.stem

    inkscape_version = subprocess.check_output(
        ["inkscape", "--version"], universal_newlines=True
    )
    log.debug(inkscape_version)

    # Convert
    # - 'Inkscape 0.92.4 (unknown)' to [0, 92, 4]
    # - 'Inkscape 1.1-dev (3a9df5bcce, 2020-03-18)' to [1, 1]
    # - 'Inkscape 1.0rc1' to [1, 0]
    v_inkscape: str = re.findall(r"[0-9.]+", inkscape_version)[0]
    inkscape_version_number = [int(part) for part in v_inkscape.split(".")]

    # Right-pad the array with zeros (so [1, 1] becomes [1, 1, 0])
    inkscape_version_number = inkscape_version_number + [0] * (
        3 - len(inkscape_version_number)
    )

    # Tuple comparison is like version comparison
    if inkscape_version_number < [1, 0, 0]:
        command = [
            "inkscape",
            "--export-area-page",
            "--export-dpi",
            "300",
            "--export-pdf",
            pdf_path,
            "--export-latex",
            filepath,
        ]
    else:
        command = [
            "inkscape",
            filepath,
            "--export-area-page",
            "--export-dpi",
            "300",
            "--export-type=pdf",
            "--export-latex",
            "--export-filename",
            pdf_path,
        ]

    log.debug("Running command:")
    log.debug(" ".join(str(e) for e in command))

    # Recompile the svg file
    completed_process = subprocess.run(command)

    if completed_process.returncode != 0:
        log.error("Return code %s", completed_process.returncode)
    else:
        log.debug("Command succeeded")

    # Copy the LaTeX code to include the file to the clipboard
    pyperclip.copy(latex_template(name, beautify(name)))


def maybe_recompile_figure(filepath: str | Path) -> None:
    """
    Recompile the figure if it is an svg file.

    Parameters
    ----------
    filepath : str or Path
        Path to the file that has changed.

    """
    filepath = Path(filepath)

    # A file has changed
    if filepath.suffix not in (".svg", ".afdesign"):
        log.debug(f"File has changed, but is supported {filepath.suffix}")
        return

    if filepath.suffix == ".afdesign":
        log.info("Converting to SVG %s", filepath)
        afdesign_to_svg(filepath)

    if filepath.suffix != ".svg":
        log.info(f"Recompiling {filepath}")
        convert_svg_to_pdf_tex(filepath)

    # Copy the LaTeX code to include the file to the clipboard
    pyperclip.copy(latex_template(filepath.stem, beautify(filepath.stem)))


def watch_daemon_inotify():
    import inotify.adapters
    from inotify.constants import IN_CLOSE_WRITE

    while True:
        roots = get_roots()

        # Watch the file with contains the paths to watch
        # When this file changes, we update the watches.
        i = inotify.adapters.Inotify()
        i.add_watch(str(roots_file), mask=IN_CLOSE_WRITE)

        # Watch the actual figure directories
        log.info("Watching directories: " + ", ".join(get_roots()))
        for root in roots:
            try:
                i.add_watch(root, mask=IN_CLOSE_WRITE)
            except Exception:
                log.debug("Could not add root %s", root)

        for event in i.event_gen(yield_nones=False):
            (_, type_names, path, filename) = event

            # If the file containing figure roots has changes, update the
            # watches
            if path == str(roots_file):
                log.info("The roots file has been updated. Updating watches.")
                for root in roots:
                    try:
                        i.remove_watch(root)
                        log.debug("Removed root %s", root)
                    except Exception:
                        log.debug("Could not remove root %s", root)
                # Break out of the loop, setting up new watches.
                break

            # A file has changed
            path = Path(path) / filename
            maybe_recompile_figure(path)


def watch_daemon_fswatch_old():
    while True:
        roots = get_roots()
        log.info("Watching directories: " + ", ".join(roots))
        # Watch the figures directories, as weel as the config directory
        # containing the roots file (file containing the figures to the figure
        # directories to watch). If the latter changes, restart the watches.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            p = subprocess.Popen(
                ["fswatch", *roots, str(user_dir)],
                stdout=subprocess.PIPE,
                universal_newlines=True,
            )

        while True:
            filepath = p.stdout.readline().strip()  # type: ignore

            # If the file containing figure roots has changes, update the
            # watches
            if filepath == str(roots_file):
                log.info("The roots file has been updated. Updating watches.")
                p.terminate()
                log.debug("Removed main watch %s")
                break
            # add some throttling here to avoid multiple recompilations
            # when the same file is saved multiple times in quick succession
            # (e.g. by an editor)
            maybe_recompile_figure(filepath)


def watch_daemon_fswatch():
    # Dictionary to track pending compilations
    pending_timers = {}
    debounce_seconds = 1.0  # Wait time after last change

    def debounced_compile(filepath: str | Path):
        """Compile after debounce period with no new changes"""
        if filepath in pending_timers:
            del pending_timers[filepath]
        maybe_recompile_figure(filepath)

    while True:
        roots = get_roots()
        log.info("Watching directories: " + ", ".join(roots))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            p = subprocess.Popen(
                ["fswatch", *roots, str(user_dir)],
                stdout=subprocess.PIPE,
                universal_newlines=True,
            )
        while True:
            filepath = p.stdout.readline().strip()  # type: ignore  # noqa
            if filepath == str(roots_file):
                log.info("The roots file has been updated. Updating watches.")
                p.terminate()
                log.debug("Removed main watch %s")
                # Cancel all pending timers
                for timer in pending_timers.values():
                    timer.cancel()
                pending_timers.clear()
                break

            # Cancel existing timer for this file if any
            if filepath in pending_timers:
                pending_timers[filepath].cancel()
                log.debug(f"Resetting debounce timer for {filepath}")

            # Schedule new compilation after debounce period
            timer = Timer(debounce_seconds, debounced_compile, args=[filepath])
            pending_timers[filepath] = timer
            timer.start()


@cli.command()
@click.argument("title")
@click.argument(
    "root",
    default=os.getcwd(),
    type=click.Path(exists=False, file_okay=False, dir_okay=True),
)
def create(title: str, root: str) -> None:
    """
    Creates a figure.

    First argument is the title of the figure
    Second argument is the figure directory.

    """
    title = title.strip()
    file_name = title.replace(" ", "-").lower() + ".svg"
    figures = Path(root).absolute()
    if not figures.exists():
        figures.mkdir()

    figure_path = figures / file_name

    # If a file with this name already exists, append a '2'.
    if figure_path.exists():
        print(title + " 2")
        return

    _ = copy(str(template), str(figure_path))
    add_root(figures)
    open_svg_file(figure_path)

    # Print the code for including the figure to stdout.
    # Copy the indentation of the input.
    leading_spaces = len(title) - len(title.lstrip())
    print(
        indent(
            latex_template(figure_path.stem, title),
            indentation=leading_spaces,
        )
    )


def open_in_affinity_designer(path: str | Path) -> None:
    """Open an .afdesign file in Affinity Designer (macOS)."""
    with warnings.catch_warnings():
        # leaving a subprocess running after interpreter exit raises a
        # warning in Python3.7+
        warnings.simplefilter("ignore", ResourceWarning)
        _ = subprocess.Popen(["open", "-a", "Affinity Designer 2", path])


def open_in_sketch(path: str | Path) -> None:
    """Open a .sketch file in Sketch (macOS)."""
    with warnings.catch_warnings():
        # leaving a subprocess running after interpreter exit raises a
        # warning in Python3.7+
        warnings.simplefilter("ignore", ResourceWarning)
        _ = subprocess.Popen(["open", "-a", "Sketch", path])


def open_in_illustrator(path: str | Path) -> None:
    """Open an .ai file in Adobe Illustrator (macOS)."""
    with warnings.catch_warnings():
        # leaving a subprocess running after interpreter exit raises a
        # warning in Python3.7+
        warnings.simplefilter("ignore", ResourceWarning)
        _ = subprocess.Popen(["open", "-a", "Adobe Illustrator", path])


def open_in_inkscape(path: str | Path) -> None:
    """Open an .svg file in Inkscape."""
    with warnings.catch_warnings():
        # leaving a subprocess running after interpreter exit raises a
        # warning in Python3.7+
        warnings.simplefilter("ignore", ResourceWarning)
        _ = subprocess.Popen(["inkscape", str(path)])


def open_svg_file(path: str | Path) -> None:
    open_in_affinity_designer(path)


def afdesign_to_svg(filepath: str | Path) -> None:
    filepath = Path(filepath).absolute()
    export_folder = filepath.parent
    export_path = export_folder / f"{filepath.stem}.svg"
    # delete export_path if it exists
    if export_path.exists():
        log.info(f"Deleting existing SVG {export_path}")
        os.remove(export_path)

    applescript = f"""
    -- Focus Affinity Designer
    tell application "Affinity Designer 2"
        activate
    end tell

    -- Save current alert volume
    set originalVolume to alert volume of (get volume settings)
    -- Mute alert volume
    set volume alert volume 0
    -- display dialog "No document window is open in Affinity Designer 2"
    delay 0.1

    -- Open the .afdesign file and save as SVG
    tell application "System Events"
        tell process "Affinity Designer 2"
            -- Export to SVG
            keystroke "s" using {{command down, shift down, option down}}
            delay 0.2
            -- Choose SVG tab
            keystroke "2" using {{command down}}
            delay 0.2
            -- Press Export button (Enter key)
            try
                click button "Export" of window 1
            end try
            -- Navigate to the same directory using Cmd+Shift+G (Go to folder)
            keystroke "g" using {{command down, shift down}}
            delay 1
            -- Type the directory path
            keystroke "{export_folder}"
            delay 0.2
            keystroke return
            delay 0.2



            delay 0.2
            -- Handle the macOS Save dialog
            click button "Save" of splitter group 1 of sheet 1 of window 1
            -- delay 0.2
            -- try
            --     click button "Replace" of sheet 1 of sheet 1 of window 1
            -- end try
        end tell
    end tell

    delay 0.2

    -- Restore original alert volume
    set volume alert volume originalVolume
    """
    _ = subprocess.run(["osascript", "-e", applescript])


@cli.command()
@click.argument(
    "root",
    default=os.getcwd(),
    type=click.Path(exists=True, file_okay=True, dir_okay=True),
)
def edit(root: str) -> None:
    """
    Edits a figure.

    Parameters
    ----------
    root : str
        Either a directory containing figures, or a path to an svg file. If a
        directory is given, a selection dialog is shown to pick a figure to
        edit.

    Notes
    -----
    On Linux, the selection dialog uses `rofi` if available, otherwise it
    falls back to a simple terminal interface. On MacOS, the selection dialog
    uses `osascript` to show a dialog.
    """

    selected: bool = False
    if root.endswith(".svg"):
        # If the user passed an svg file, just open it.
        figures = Path(root).absolute().parent
        path = Path(root).absolute()
        selected = True
    else:
        figures = Path(root).absolute()

        # Find svg files and sort them
        files = figures.glob("*.svg")
        files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)

        # Open a selection dialog using a gui picker like rofi
        names = [beautify(f.stem) for f in files]
        _, index, selected = pick(names)
        path = files[index]
    if selected:
        add_root(figures)
        open_svg_file(path)


if __name__ == "__main__":
    cli()
