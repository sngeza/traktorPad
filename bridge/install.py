#!/usr/bin/env python3
"""Install or remove the TraktorPad bridge inside Traktor Pro's QML folder.

    python3 bridge/install.py install     # add the bridge (quit Traktor first)
    python3 bridge/install.py uninstall   # remove it again
    python3 bridge/install.py status

It makes two changes, both reversible:
  1. copies bridge/qml/TraktorPad into  <Traktor>/qml/CSI/Common/TraktorPad
  2. adds two marked lines to one controller mapping (CSI/D2/D2.qml by default)
     so Traktor loads the bridge. The original file is kept next to it as
     <name>.qml.traktorpad-backup.

A Traktor update replaces the app, so run "install" again after updating.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

MARK = "// TraktorPad"
IMPORT_LINE = 'import "../Common/TraktorPad" ' + MARK
MODULE_LINE = '  TraktorPadModule { name: "traktorpad" } ' + MARK
SOURCE = Path(__file__).resolve().parent / "qml" / "TraktorPad"
BACKUP_SUFFIX = ".traktorpad-backup"

APP_GLOBS = [
    "/Applications/Native Instruments/Traktor Pro 4/Traktor Pro 4.app",
    "/Applications/Native Instruments/Traktor Pro 3/Traktor.app",
    "/Applications/Native Instruments/Traktor Pro*/Traktor*.app",
]


def find_qml_dir(explicit):
    """Return the qml directory, from --qml-dir/--app or the usual install paths."""
    if explicit:
        path = Path(explicit).expanduser()
        candidates = [path, path / "Contents" / "Resources" / "qml"]
    else:
        candidates = []
        for pattern in APP_GLOBS:
            root = Path("/")
            for app in sorted(root.glob(pattern.lstrip("/")), reverse=True):
                candidates.append(app / "Contents" / "Resources" / "qml")
    for candidate in candidates:
        if (candidate / "CSI").is_dir():
            return candidate
    return None


def mapping_file(qml_dir, controller):
    return qml_dir / "CSI" / controller / (controller + ".qml")


def strip_marked(text):
    return "".join(line for line in text.splitlines(keepends=True) if MARK not in line)


def patch(text):
    """Insert the import and the module into a controller mapping's source."""
    text = strip_marked(text)
    newline = "\r\n" if "\r\n" in text else "\n"

    imports = list(re.finditer(r"^import .*$", text, re.MULTILINE))
    if not imports:
        raise ValueError("no import lines found")
    end = imports[-1].end()
    text = text[:end] + newline + IMPORT_LINE + text[end:]

    # The root element is "Mapping {" with the brace on the same or the next line.
    root = re.search(r"^Mapping\s*\{[ \t]*\r?$", text, re.MULTILINE)
    if not root:
        raise ValueError("no top-level 'Mapping {' block found")
    return text[:root.end()] + newline + MODULE_LINE + text[root.end():]


def install(qml_dir, controller):
    target = mapping_file(qml_dir, controller)
    if not target.is_file():
        available = sorted(p.name for p in (qml_dir / "CSI").iterdir()
                           if (p / (p.name + ".qml")).is_file())
        sys.exit("No mapping for controller %r. Available: %s"
                 % (controller, ", ".join(available)))

    original = target.read_text(encoding="utf-8")
    patched = patch(original)          # fail before touching anything on disk

    backup = target.with_name(target.name + BACKUP_SUFFIX)
    if not backup.exists():
        backup.write_text(strip_marked(original), encoding="utf-8")

    destination = qml_dir / "CSI" / "Common" / "TraktorPad"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(SOURCE, destination)
    target.write_text(patched, encoding="utf-8")

    print("Installed into %s" % qml_dir)
    print("  bridge files : %s" % destination)
    print("  hooked into  : %s" % target)
    print("  backup       : %s" % backup)
    print()
    print("Next: start Traktor. If you do not own a Kontrol %s, add it once under" % controller)
    print("Preferences > Controller Manager > Add... > Traktor > Kontrol %s." % controller)


def uninstall(qml_dir):
    removed = False
    for target in sorted((qml_dir / "CSI").glob("*/*.qml")):
        text = target.read_text(encoding="utf-8")
        if MARK in text:
            target.write_text(strip_marked(text), encoding="utf-8")
            print("Unhooked %s" % target)
            removed = True
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        if backup.exists():
            backup.unlink()
    destination = qml_dir / "CSI" / "Common" / "TraktorPad"
    if destination.exists():
        shutil.rmtree(destination)
        print("Removed %s" % destination)
        removed = True
    print("TraktorPad removed." if removed else "TraktorPad was not installed.")


def status(qml_dir):
    hooked = [t for t in sorted((qml_dir / "CSI").glob("*/*.qml"))
              if MARK in t.read_text(encoding="utf-8")]
    files = (qml_dir / "CSI" / "Common" / "TraktorPad").is_dir()
    print("Traktor QML : %s" % qml_dir)
    print("Bridge files: %s" % ("present" if files else "missing"))
    print("Hooked into : %s" % (", ".join(t.parent.name for t in hooked) or "nothing"))
    return 0 if files and hooked else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["install", "uninstall", "status"])
    parser.add_argument("--app", "--qml-dir", dest="location", metavar="PATH",
                        help="Traktor .app bundle or its qml folder (default: auto-detect)")
    parser.add_argument("--controller", default="D2",
                        help="controller mapping to hook into (default: D2). Use one you "
                             "have added in Traktor's Controller Manager, e.g. S8, S4MK3, X1MK3.")
    args = parser.parse_args(argv)

    qml_dir = find_qml_dir(args.location)
    if not qml_dir:
        sys.exit("Could not find Traktor's qml folder. Pass it with --app, e.g.\n"
                 "  --app '/Applications/Native Instruments/Traktor Pro 4/Traktor Pro 4.app'")

    try:
        if args.command == "install":
            install(qml_dir, args.controller)
        elif args.command == "uninstall":
            uninstall(qml_dir)
        else:
            return status(qml_dir)
    except PermissionError as error:
        sys.exit("Permission denied: %s\n"
                 "Run with sudo, and on recent macOS allow your terminal under\n"
                 "System Settings > Privacy & Security > App Management." % error.filename)
    except ValueError as error:
        sys.exit("Could not patch the mapping (%s). Nothing was changed." % error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
