from django.core.management.base import BaseCommand

from inventory.models import Consumable, ConsumableCategory, Unit

STARTER = [
    # name, category, unit
    ('Iodinated contrast (CT)', 'Contrast media', 'ml'),
    ('MRI contrast (gadolinium)', 'Contrast media', 'ml'),
    ('Oral contrast', 'Contrast media', 'ml'),
    ('Barium sulphate', 'Contrast media', 'ml'),
    ('Normal saline 0.9%', 'IV and injection supplies', 'bottle'),
    ('IV cannula', 'IV and injection supplies', 'piece'),
    ('IV extension set', 'IV and injection supplies', 'piece'),
    ('Contrast injector syringe', 'Syringes and needles', 'piece'),
    ('Syringe 10 ml', 'Syringes and needles', 'piece'),
    ('Syringe 20 ml', 'Syringes and needles', 'piece'),
    ('Needle', 'Syringes and needles', 'piece'),
    ('Plaster (adhesive tape)', 'Dressings and tapes', 'cm'),
    ('Gauze', 'Dressings and tapes', 'pack'),
    ('Alcohol swab', 'Dressings and tapes', 'piece'),
    ('Gloves', 'Gloves and protection', 'pair'),
    ('Ultrasound gel', 'Ultrasound supplies', 'ml'),
    ('Transducer cover', 'Ultrasound supplies', 'piece'),
    ('Probe condom / sheath', 'Ultrasound supplies', 'piece'),
    ('Paper roll (couch cover)', 'Patient comfort', 'cm'),
    ('Disinfectant wipes', 'Cleaning and disinfection', 'pack'),
]


class Command(BaseCommand):
    help = 'Add an editable starter list of common consumables (skips ones that already exist).'

    def handle(self, *args, **opts):
        n = 0
        for name, cat, unit in STARTER:
            _, created = Consumable.objects.get_or_create(
                name=name, defaults={'category': ConsumableCategory.objects.get_or_create(name=cat)[0],
                                     'default_unit': Unit.objects.get_or_create(name=unit)[0]})
            n += created
        self.stdout.write(self.style.SUCCESS(f'{n} starter consumable(s) added.'))
