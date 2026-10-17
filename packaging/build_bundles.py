"""Build the shareable, no-install bundles (Windows .zip and Linux .tar.gz).

Each bundle contains a private copy of Python (python-build-standalone) plus the app and its
libraries, so the person receiving it installs nothing. Needs internet access and Python 3.12+ with pip.

    python packaging/build_bundles.py            # both
    python packaging/build_bundles.py windows    # or: linux
"""
import compileall
import io
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'bundles'
CACHE = Path(os.environ.get('RCM_BUILD_CACHE', ROOT / 'packaging' / '.cache'))
PBS_TAG, PY_VERSION = '20261001', '3.12.15'
PBS = 'https://github.com/astral-sh/python-build-standalone/releases/download/{tag}/cpython-{ver}%2B{tag}-{triple}-install_only_stripped.tar.gz'
TARGETS = {
    'windows': {'triple': 'x86_64-pc-windows-msvc', 'pip_platform': 'win_amd64'},
    'linux': {'triple': 'x86_64-unknown-linux-gnu', 'pip_platform': 'manylinux2014_x86_64'},
}
PACKAGES = ['Django>=5.1,<7', 'openpyxl>=3.1', 'waitress>=3.0', 'whitenoise>=6.6', 'tzdata']
SKIP = {'__pycache__', 'userdata', 'staticfiles', 'bundles', 'packaging', '.git', 'lib', 'db.sqlite3'}

README = """\
RADIOLOGY CONSUMABLES
=====================

Nothing needs to be installed. Python and everything else is already inside this folder.

{start_help}

The app opens in your web browser at http://127.0.0.1:8000/

SIGNING IN
  The first time, the page asks you to create the administrator account. Remember that password.
  The administrator adds other people under "Users" and chooses what each may do:
  everyone can view; "add data", "change consumables already entered" and "delete" are switched on per
  person. New users start view-only. Administrators can always do all of it.
  Forgotten password: the administrator sets a new one under Users > Reset password.
  If the administrator themself is locked out, run "{reset_help}" and follow the prompts.

SAVING
  On an examination page you can change many consumable rows, add as many new items as you like (required
  items, optional items and what they replace) and press the single "Save changes" button once: everything is
  saved together, or nothing if a value is wrong. Ticking "Remove" on a row, choosing an alternative with
  "Use ... instead", applying a saved set, copying from another examination and saving the result as a set are
  all done by the same button.

EXCEL EXAM VIEW
  Tools > Export to Excel > "Exam view" makes a sheet with one block per examination: modality, examination and
  machine code on the left, the required consumables in rows beside them, and the optional items shown as
  replacements if the required item is unavailable. On an examination page, mark an item Optional and choose
  which required item it "Replaces" so it lines up next to it.

ALTERNATIVES AND SAVED SETS
  When adding a consumable you can list alternative items that can replace it. On an examination page they
  are shown under the consumable. Saved sets (Consumables > Saved sets) are named lists of consumables
  built on their own and applied to any examination, or to many at once, whenever needed.

CONSUMABLES LIST FOR AUTO-FILL
  Consumables > Import list from Excel loads your Excel list of consumables. When someone adds a
  consumable and starts typing the name, matching items are suggested and picking one fills in the
  category, unit, description and alternatives. If what you type is not in the list it turns red, and it
  is still saved exactly as typed. A file named Consumables_List.xlsx placed in the "app\\data" folder
  is loaded automatically at start.
Keep the black window open while you work. Close it to stop the app.

YOUR DATA
  Everything you enter is stored in the "userdata" folder next to this file (created on first start).
  A backup copy of the database is made automatically once a day in userdata\\backups.
  To move the app to another computer WITH your data, copy this whole folder.
  To give someone a CLEAN copy, share this folder WITHOUT the "userdata" folder.

UPDATING
  Replace the "app" and "runtime" folders with the ones from the new version. Keep "userdata".

SHARING WITH OTHER COMPUTERS ON YOUR NETWORK (optional)
  Open userdata\\settings.ini, change  host = 127.0.0.1  to  host = 0.0.0.0  and restart.
  Others then open  http://<this-computer-address>:8000/  (the address is printed in the window).
  Everyone must sign in, but the connection is not encrypted, so only do this on a network you trust.

IF SOMETHING GOES WRONG
  - Windows may show a "protected your PC" notice the first time. Choose "More info" > "Run anyway".
  - If port 8000 is busy the app picks the next free one and prints it.
  - Needs Windows 10 or newer (64-bit).
"""


def download(url, dest):
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print('downloading', url.split('/')[-1])
    with urllib.request.urlopen(url, timeout=600) as r, open(dest.with_suffix('.part'), 'wb') as f:
        shutil.copyfileobj(r, f)
    dest.with_suffix('.part').rename(dest)
    return dest


def copy_app(dest):
    def ignore(d, names):
        return [n for n in names if n in SKIP or n.endswith('.pyc') or n.endswith('.sqlite3')]
    shutil.copytree(ROOT, dest, ignore=ignore)


def slim_runtime(rt, target):
    junk = ['Lib/test', 'Lib/idlelib', 'Lib/tkinter', 'Lib/turtledemo', 'include']
    if target == 'windows':
        junk += ['tcl', 'libs', 'DLLs/_tkinter.pyd', 'DLLs/tcl86t.dll', 'DLLs/tk86t.dll']
    else:
        junk += ['lib/python3.12/test', 'lib/python3.12/idlelib', 'lib/python3.12/tkinter',
                 'lib/python3.12/turtledemo', 'lib/tcl8.6', 'lib/tk8.6', 'lib/itcl4.3.2', 'lib/tcl9.0', 'lib/tk9.0']
        junk = [j for j in junk if j not in ('Lib/test', 'Lib/idlelib', 'Lib/tkinter', 'Lib/turtledemo')]
    for j in junk:
        p = rt / j
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()


def build(target, staged_app):
    cfg = TARGETS[target]
    work = OUT / f'_work_{target}'
    shutil.rmtree(work, ignore_errors=True)
    top = work / 'RadiologyConsumables'
    top.mkdir(parents=True)

    archive = download(PBS.format(tag=PBS_TAG, ver=PY_VERSION, triple=cfg['triple']),
                       CACHE / f'python-{target}.tar.gz')
    with tarfile.open(archive) as t:
        t.extractall(work / '_py')
    shutil.move(str(work / '_py' / 'python'), str(top / 'runtime'))
    shutil.rmtree(work / '_py')
    slim_runtime(top / 'runtime', target)

    shutil.copytree(staged_app, top / 'app')
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', '--no-compile',
                           '--target', str(top / 'app' / 'lib'), '--platform', cfg['pip_platform'],
                           '--python-version', '3.12', '--implementation', 'cp', '--only-binary=:all:'] + PACKAGES)
    shutil.rmtree(top / 'app' / 'lib' / 'bin', ignore_errors=True)
    if target == 'windows':
        shutil.rmtree(top / 'app' / 'lib' / 'bin', ignore_errors=True)
    # Same minor version of Python => the .pyc files work on both platforms; this makes the first start quick.
    compileall.compile_dir(str(top / 'app'), quiet=1, force=True)

    if target == 'windows':
        start = top / 'Start Radiology Consumables.bat'
        start.write_bytes(('@echo off\r\ntitle Radiology consumables\r\ncd /d "%~dp0"\r\n'
                           '"runtime\\python.exe" -I "app\\run_app.py"\r\nif errorlevel 1 pause\r\n').encode())
        help_text = 'TO START: double-click "Start Radiology Consumables.bat"'
        (top / 'Reset password.bat').write_bytes(
            ('@echo off\r\ncd /d "%~dp0"\r\nset /p U=Username to reset: \r\n'
             '"runtime\\python.exe" -I "app\\run_app.py" --reset-password "%U%"\r\npause\r\n').encode())
        (top / 'README.txt').write_bytes(README.format(start_help=help_text, reset_help='Reset password.bat')
                                         .replace('\n', '\r\n').encode())
        archive_path = OUT / 'RadiologyConsumables-Windows.zip'
        archive_path.unlink(missing_ok=True)
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for p in sorted(work.rglob('*')):
                if p.is_file():
                    z.write(p, p.relative_to(work))
    else:
        start = top / 'start.sh'
        start.write_text('#!/bin/sh\ncase "$0" in */*) cd "${0%/*}" || exit 1;; esac\nexec ./runtime/bin/python3 -I app/run_app.py\n')
        start.chmod(0o755)
        help_text = 'TO START: run  ./start.sh  (or double-click it and choose "Run")'
        reset = top / 'reset-password.sh'
        reset.write_text('#!/bin/sh\ncase "$0" in */*) cd "${0%/*}" || exit 1;; esac\n'
                         'printf "Username to reset: "; read U\n'
                         'exec ./runtime/bin/python3 -I app/run_app.py --reset-password "$U"\n')
        reset.chmod(0o755)
        (top / 'README.txt').write_text(README.format(start_help=help_text, reset_help='./reset-password.sh')
                                        .replace('\\', '/'))
        archive_path = OUT / 'RadiologyConsumables-Linux.tar.gz'
        with tarfile.open(archive_path, 'w:gz', compresslevel=9) as t:
            t.add(top, arcname='RadiologyConsumables')
    shutil.rmtree(work)
    print(f'built {archive_path.name}  {archive_path.stat().st_size / 1e6:.0f} MB')


def main():
    targets = sys.argv[1:] or list(TARGETS)
    OUT.mkdir(exist_ok=True)
    staged = OUT / '_staged_app'
    shutil.rmtree(staged, ignore_errors=True)
    copy_app(staged)
    # collect static files once (needs Django available to this Python)
    env = dict(os.environ, RCM_DATA_DIR=str(OUT / '_tmp_data'))
    subprocess.check_call([sys.executable, 'manage.py', 'collectstatic', '--noinput', '-v0'], cwd=staged, env=env)
    shutil.rmtree(OUT / '_tmp_data', ignore_errors=True)
    for t in targets:
        build(t, staged)
    shutil.rmtree(staged, ignore_errors=True)


if __name__ == '__main__':
    main()
