from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from inventory.catalog import import_catalog


class Command(BaseCommand):
    help = 'Import the consumables list (Excel) used for auto-fill when adding consumables (safe to re-run).'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--add', action='store_true',
                            help='Also create every item as a consumable right away.')

    def handle(self, *args, **opts):
        path = Path(opts['path'])
        if not path.exists():
            raise CommandError(f'File not found: {path}')
        r = import_catalog(path, add_as_consumables=opts['add'])
        self.stdout.write(self.style.SUCCESS(
            f"Consumables list: {r['new']} new, {r['updated']} updated, {r['skipped']} rows skipped"
            + (f", {r['consumables_added']} added as consumables" if opts['add'] else '')))
