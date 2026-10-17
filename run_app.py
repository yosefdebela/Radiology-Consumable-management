"""Launcher for the standalone app: prepares the database, starts the web server, opens the browser.

Used by the Start scripts in the shareable bundle. Developers can also run: python run_app.py
"""
import configparser
import os
import socket
import sqlite3
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BUNDLE_DIR = APP_DIR.parent if (APP_DIR.parent / 'runtime').is_dir() else None

if (APP_DIR / 'lib').is_dir():          # packages bundled next to the app
    sys.path.insert(0, str(APP_DIR / 'lib'))
sys.path.insert(0, str(APP_DIR))

DATA_DIR = Path(os.environ.get('RCM_DATA_DIR') or ((BUNDLE_DIR or APP_DIR) / 'userdata'))
os.environ['RCM_DATA_DIR'] = str(DATA_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
DATA_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_TEXT = """\
# Radiology consumables - settings. Restart the app after changing this file.
[server]
# 127.0.0.1 = only this computer can open the app.
# 0.0.0.0   = other computers on the same network can open it too, at http://<this-pc-address>:<port>/
#             (everyone has to sign in, but the connection is not encrypted, so only do this on a trusted network).
host = 127.0.0.1
port = 8000
open_browser = yes
"""


def say(msg=''):
    print(msg, flush=True)


def load_settings():
    path = DATA_DIR / 'settings.ini'
    if not path.exists():
        path.write_text(SETTINGS_TEXT, encoding='utf-8')
    cfg = configparser.ConfigParser()
    cfg.read(path, encoding='utf-8')
    sec = cfg['server'] if cfg.has_section('server') else {}
    host = sec.get('host', '127.0.0.1').strip() or '127.0.0.1'
    try:
        port = int(sec.get('port', '8000'))
    except ValueError:
        port = 8000
    return host, port, str(sec.get('open_browser', 'yes')).strip().lower() in ('yes', 'true', '1', 'on')


def backup_database():
    """One consistent copy of the database per day; the 14 newest are kept."""
    db = DATA_DIR / 'db.sqlite3'
    if not db.exists() or db.stat().st_size == 0:
        return
    folder = DATA_DIR / 'backups'
    folder.mkdir(exist_ok=True)
    target = folder / f'db-{datetime.now():%Y-%m-%d}.sqlite3'
    if target.exists():
        return
    src, dst = sqlite3.connect(db), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in sorted(folder.glob('db-*.sqlite3'))[:-14]:
        old.unlink()


def prepare_data():
    import django
    from django.core.management import call_command
    django.setup()
    backup_database()
    call_command('migrate', interactive=False, verbosity=0)
    marker = DATA_DIR / '.initialized'
    if not marker.exists():
        from inventory.models import Examination
        if not Examination.objects.exists():
            xlsx = APP_DIR / 'data' / 'Radiology_Investigations_Categorized.xlsx'
            if xlsx.exists():
                say('First start: loading the examination list...')
                call_command('import_examinations', str(xlsx), verbosity=0)
            call_command('seed_consumables', verbosity=0)
        marker.write_text('done')
    # An Excel list of consumables placed in app/data is loaded (again) whenever it changes. It only feeds the
    # suggestions on the Add consumable form; nothing is added to the consumables themselves.
    catalog = APP_DIR / 'data' / 'Consumables_List.xlsx'
    if catalog.exists():
        import hashlib
        digest = hashlib.sha256(catalog.read_bytes()).hexdigest()
        stamp = DATA_DIR / '.catalog_imported'
        if not stamp.exists() or stamp.read_text().strip() != digest:
            say('Loading the consumables list...')
            call_command('import_consumable_catalog', str(catalog), verbosity=0)
            stamp.write_text(digest)


def is_our_app(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2) as r:
            return b'name="application-name" content="Radiology consumables"' in r.read(60000)
    except Exception:
        return False


def port_is_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name != 'nt':   # a just-closed port lingers on Linux; on Windows this flag would hide a real clash
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def lan_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('10.255.255.255', 1))
            return s.getsockname()[0]
    except OSError:
        return 'this-pc-address'


def open_when_ready(url, port):
    for _ in range(100):
        try:
            socket.create_connection(('127.0.0.1', port), timeout=0.3).close()
            webbrowser.open(url)
            return
        except OSError:
            time.sleep(0.2)


def reset_password(username):
    """Command-line recovery: set a new password for an account (used when the only administrator is locked out)."""
    import getpass
    import django
    django.setup()
    from django.contrib.auth import get_user_model, password_validation
    from django.core.exceptions import ValidationError
    user = get_user_model().objects.filter(username__iexact=username.strip()).first()
    if user is None:
        say(f'There is no account named "{username}".')
        return 1
    pw1, pw2 = getpass.getpass('New password: '), getpass.getpass('Repeat the new password: ')
    if pw1 != pw2:
        say('The two passwords do not match. Nothing was changed.')
        return 1
    try:
        password_validation.validate_password(pw1, user)
    except ValidationError as e:
        say('That password is not accepted: ' + ' '.join(e.messages))
        return 1
    user.set_password(pw1)
    user.save(update_fields=['password'])
    say(f'Password changed for {user.username}. You can sign in now.')
    return 0


def main():
    host, port, want_browser = load_settings()
    check_host = '127.0.0.1' if host in ('0.0.0.0', '') else host
    if not port_is_free(host, port):
        if is_our_app(port):
            say(f'Radiology consumables is already running. Opening http://127.0.0.1:{port}/')
            if want_browser:
                webbrowser.open(f'http://127.0.0.1:{port}/')
            return
        for candidate in range(port + 1, port + 21):
            if port_is_free(host, candidate):
                say(f'Port {port} is used by another program, using {candidate} instead.')
                port = candidate
                break
        else:
            say(f'No free port found near {port}. Change the port in {DATA_DIR / "settings.ini"}.')
            return

    say('Radiology consumables')
    say('=' * 40)
    say('Getting things ready...')
    prepare_data()

    from waitress import serve
    from config.wsgi import application

    url = f'http://127.0.0.1:{port}/'
    say()
    say(f'  The app is running at {url}')
    if host == '0.0.0.0':
        say(f'  Other computers on the network: http://{lan_address()}:{port}/')
    say(f'  Your data is stored in: {DATA_DIR}')
    from django.contrib.auth import get_user_model
    if not get_user_model().objects.exists():
        say('  First time: the page will ask you to create the administrator account.')
    say()
    say('  Keep this window open while you work. Close it to stop the app.')
    say()
    if want_browser:
        threading.Thread(target=open_when_ready, args=(url, port), daemon=True).start()
    try:
        serve(application, host=host, port=port, threads=8, ident='radiology-consumables', _quiet=True)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    try:
        if len(sys.argv) >= 3 and sys.argv[1] == '--reset-password':
            sys.exit(reset_password(sys.argv[2]))
        main()
    except Exception as exc:  # keep the window readable on failure
        import traceback
        traceback.print_exc()
        say(f'\nThe app could not start: {exc}')
        if os.name == 'nt':
            input('Press Enter to close...')
        sys.exit(1)
