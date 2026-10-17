from django.db import migrations

MODALITIES = [('CT Scan', 'ct'), ('MRI', 'mri'), ('Ultrasound', 'ultrasound'),
              ('X-Ray', 'x-ray'), ('Special X-Ray Procedures', 'special-x-ray')]
UNITS = ['ml', 'vial', 'piece', 'pair', 'bottle', 'pack', 'cm', 'roll', 'box', 'set']
CATEGORIES = ['Contrast media', 'IV and injection supplies', 'Syringes and needles', 'Dressings and tapes',
              'Gloves and protection', 'Ultrasound supplies', 'Patient comfort', 'Cleaning and disinfection',
              'Other']


def seed(apps, schema_editor):
    Modality = apps.get_model('inventory', 'Modality')
    Unit = apps.get_model('inventory', 'Unit')
    Cat = apps.get_model('inventory', 'ConsumableCategory')
    for i, (name, slug) in enumerate(MODALITIES, start=1):
        Modality.objects.get_or_create(slug=slug, defaults={'name': name, 'sort_order': i})
    for u in UNITS:
        Unit.objects.get_or_create(name=u)
    for c in CATEGORIES:
        Cat.objects.get_or_create(name=c)


class Migration(migrations.Migration):
    dependencies = [('inventory', '0001_initial')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
