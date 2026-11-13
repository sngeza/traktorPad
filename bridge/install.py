#!/usr/bin/env python3
"""Install or remove the TraktorPad bridge inside Traktor Pro's QML folder.

    python3 bridge/install.py install     # add the bridge (quit Traktor first)
    python3 bridge/install.py uninstall   # remove it again
    python3 bridge/install.py status

It makes these changes, all reversible:
  1. copies the bridge into  <Traktor>/qml/CSI/Common/TraktorPad  and
     <Traktor>/qml/Screens/TraktorPad
  2. adds two marked lines to one controller mapping (CSI/D2/D2.qml by default)
     so Traktor loads the deck bridge
  3. adds two marked lines to that controller's screen file so Traktor loads
     the browser bridge
Each changed file keeps its original next to it as <name>.traktorpad-backup.

A Traktor update replaces the app, so run "install" again after updating.
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

MARK = "// TraktorPad"
SOURCE = Path(__file__).resolve().parent / "qml"
BACKUP_SUFFIX = ".traktorpad-backup"

# Where the bridge files go, relative to Traktor's qml folder.
MODULE_DIR = Path("CSI/Common/TraktorPad")
SCREEN_DIR = Path("Screens/TraktorPad")

# The screen QML each display controller uses. Traktor's browser can only be
# reached from there; other controllers get the deck display without a browser.
SCREEN_FILES = {
    "D2": Path("Screens/S8/Views/Screen.qml"),
    "S8": Path("Screens/S8/Views/Screen.qml"),
    "S5": Path("Screens/S8/Views/Screen.qml"),
    "S4MK3": Path("Screens/S4MK3/Screen.qml"),
}

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


def patch(text, import_line, element_line, root):
    """Insert an import and one element into the root object of a QML file."""
    text = strip_marked(text)
    newline = "\r\n" if "\r\n" in text else "\n"

    imports = list(re.finditer(r"^import .*$", text, re.MULTILINE))
    if not imports:
        raise ValueError("no import lines found")
    end = imports[-1].end()
    text = text[:end] + newline + import_line + " " + MARK + text[end:]

    # The root element, with its brace on the same or the next line.
    opening = re.search(r"^%s\s*\{[ \t]*\r?$" % re.escape(root), text, re.MULTILINE)
    if not opening:
        raise ValueError("no top-level '%s {' block found" % root)
    return (text[:opening.end()] + newline + "  " + element_line + " " + MARK
            + text[opening.end():])


def hooks(qml_dir, controller):
    """The files to patch: (file, import line, element line, root element)."""
    mapping = mapping_file(qml_dir, controller)
    found = [(mapping, 'import "../Common/TraktorPad"',
              'TraktorPadModule { name: "traktorpad" }', "Mapping")]
    screen = SCREEN_FILES.get(controller)
    if screen and (qml_dir / screen).is_file():
        relative = os.path.relpath(qml_dir / SCREEN_DIR, (qml_dir / screen).parent)
        found.append((qml_dir / screen,
                      "import '%s' as TraktorPad" % relative.replace(os.sep, "/"),
                      "TraktorPad.PadBrowser {}", "Item"))
    return found


def copy_bridge(qml_dir):
    for relative in (MODULE_DIR, SCREEN_DIR):
        destination = qml_dir / relative
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(SOURCE / relative, destination)
    # The browser bridge uses the same transport as the deck bridge.
    shutil.copy2(SOURCE / MODULE_DIR / "PadLink.qml", qml_dir / SCREEN_DIR / "PadLink.qml")


def patched_files(qml_dir):
    found = []
    for folder in ("CSI", "Screens"):
        for target in sorted((qml_dir / folder).rglob("*.qml")):
            if MODULE_DIR in target.relative_to(qml_dir).parents:
                continue
            if SCREEN_DIR in target.relative_to(qml_dir).parents:
                continue
            if MARK in target.read_text(encoding="utf-8", errors="replace"):
                found.append(target)
    return found


def install(qml_dir, controller):
    target = mapping_file(qml_dir, controller)
    if not target.is_file():
        available = sorted(p.name for p in (qml_dir / "CSI").iterdir()
                           if (p / (p.name + ".qml")).is_file())
        sys.exit("No mapping for controller %r. Available: %s"
                 % (controller, ", ".join(available)))

    # Work out every change first, so a failure leaves Traktor untouched.
    plan = []
    for path, import_line, element_line, root in hooks(qml_dir, controller):
        original = path.read_text(encoding="utf-8")
        plan.append((path, original, patch(original, import_line, element_line, root)))

    # Only one controller should carry the bridge.
    for other in patched_files(qml_dir):
        other.write_text(strip_marked(other.read_text(encoding="utf-8")), encoding="utf-8")

    copy_bridge(qml_dir)
    for path, original, patched in plan:
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if not backup.exists():
            backup.write_text(strip_marked(original), encoding="utf-8")
        path.write_text(patched, encoding="utf-8")

    print("Installed into %s" % qml_dir)
    for path, _, _ in plan:
        print("  hooked into  : %s" % path.relative_to(qml_dir))
    if len(plan) < 2:
        print("  note         : Kontrol %s has no screen QML that can reach Traktor's" % controller)
        print("                 browser, so the iPad will show decks only. Use D2 or S8")
        print("                 for the browser.")
    print()
    print("Next: start Traktor. If you do not own a Kontrol %s, add it once under" % controller)
    print("Preferences > Controller Manager > Add... > Traktor > Kontrol %s." % controller)


def uninstall(qml_dir):
    removed = False
    for target in patched_files(qml_dir):
        target.write_text(strip_marked(target.read_text(encoding="utf-8")), encoding="utf-8")
        print("Unhooked %s" % target.relative_to(qml_dir))
        removed = True
    for folder in ("CSI", "Screens"):
        for backup in (qml_dir / folder).rglob("*" + BACKUP_SUFFIX):
            backup.unlink()
    for relative in (MODULE_DIR, SCREEN_DIR):
        if (qml_dir / relative).exists():
            shutil.rmtree(qml_dir / relative)
            print("Removed %s" % relative)
            removed = True
    print("TraktorPad removed." if removed else "TraktorPad was not installed.")


def status(qml_dir):
    hooked = patched_files(qml_dir)
    files = (qml_dir / MODULE_DIR).is_dir() and (qml_dir / SCREEN_DIR).is_dir()
    print("Traktor QML : %s" % qml_dir)
    print("Bridge files: %s" % ("present" if files else "missing"))
    print("Hooked into : %s" % (", ".join(str(t.relative_to(qml_dir)) for t in hooked) or "nothing"))
    return 0 if files and hooked else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["install", "uninstall", "status"])
    parser.add_argument("--app", "--qml-dir", dest="location", metavar="PATH",
                        help="Traktor .app bundle or its qml folder (default: auto-detect)")
    parser.add_argument("--controller", default="D2",
                        help="controller mapping to hook into (default: D2). Use one you "
                             "have added in Traktor's Controller Manager. D2, S8, S5 and "
                             "S4MK3 also provide the browser.")
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
