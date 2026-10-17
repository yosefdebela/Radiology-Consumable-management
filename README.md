# Radiology Examination and Consumables Management System

## Sharing it with people who have no Python

Build the ready-to-share packages (needs internet once, and Python 3.12+ on the build machine):

    python packaging/build_bundles.py            # writes bundles/RadiologyConsumables-Windows.zip and -Linux.tar.gz

The recipient unzips the folder and double-clicks `Start Radiology Consumables.bat` (Windows) or runs
`./start.sh` (Linux). Python and all libraries are inside the folder. Data lives in the `userdata` folder
next to the app, with an automatic daily database backup. See README.txt inside the bundle.

Developers can still run it directly (below). Dev data now lives in `userdata/` instead of `db.sqlite3`.

Django app for recording which consumables each radiology examination uses.
No internet access is needed at runtime (no CDN assets).

## Run it

    pip install -r requirements.txt
    python manage.py migrate                # creates tables + modalities, units, categories
    python manage.py import_examinations    # loads data/Radiology_Investigations_Categorized.xlsx (315 exams)
    python manage.py seed_consumables       # optional: 20 editable starter consumables
    python manage.py createsuperuser        # optional: for /admin/
    python manage.py runserver

Open http://127.0.0.1:8000/

`import_examinations` is safe to re-run (it matches on the exam code). Pass another
path to import a different workbook: `python manage.py import_examinations path/to/file.xlsx`.

## Models (inventory/models.py)

- Modality: CT Scan, MRI, Ultrasound, X-Ray, Special X-Ray Procedures
- Examination: code, name, modality, subcategory, price (optional), notes, is_active
- ConsumableCategory, Unit (ml, vial, piece, pair, bottle, pack, cm, roll, box, set; add more under Setup > Units).
  Quantities are whole numbers by default; edit a unit to allow decimals.
- Consumable: name, category, default unit, description, is_active
- ExaminationConsumable: examination, consumable, quantity, unit, is_required, notes
  (one row per consumable per examination)

## Signing in, roles and permissions

- The first visit asks for the **administrator** account (one-time setup page). No default password exists.
- **Administrators** do everything and manage people under Users: add accounts, reset passwords, deactivate
  accounts (never deleted) and choose what each standard user may do.
- **Standard users** can always view and export. Three switches, off by default, widen that:
  *Can add data*, *Can change consumables that were already entered* (quantities, units, notes, names,
  alternatives, swapping an alternative in) and *Can delete data*. Change and delete both need "add". Hidden buttons are only a convenience:
  every change is checked again on the server (`inventory/permissions.py`).
- Five wrong passwords in a row lock that name from that computer for 15 minutes.
- Locked-out administrator: run `Reset password.bat` (Windows) / `./reset-password.sh` (Linux) in the bundle, or
  `python run_app.py --reset-password <username>` from the source.

## One Save button per page

An examination page (and a saved set's page) saves **everything on it** with a single **Save changes** button:
- changes to any number of existing consumable rows (quantity, unit, required/optional, *Replaces*, notes);
- any number of new items from the *Add consumables* table (*Add another row* for more): required items, optional
  items and which required item each optional one replaces, including a required item typed in the same table;
- on an examination page, the choices under *Also when you save*: apply a saved set, copy from another
  examination, and save the result as a reusable set.
It is all or nothing: if any value is invalid nothing is saved, and the page is shown again with every row as typed and
the problem marked. The bar turns amber while there are unsaved changes and the browser warns before leaving.
Rows can also be ticked **Remove** (struck through, removed when you save, after a confirmation) and an item with
alternatives has a *Use ... instead* choice that is applied on save. Only Deactivate/Delete of the examination itself
stay separate actions. The page forms use `novalidate`: the server does all checking, because a browser silently
refuses to submit a form that holds an empty "required" field (a blank *add* row used to block Save).
Saved sets can hold optional and replacement items too, and keep those links when applied or saved from an examination.

## Excel exam view

Export to Excel > *Exam view* gives one block per examination (like a printed list): modality, examination and
machine code (the examination code, e.g. L00761) in merged cells on the left; its **required** consumables in rows
beside them (alphabetical, with quantity and unit); and, in the last column, the **optional** consumables entered on
that examination as replacements if the required item is unavailable, written as `Name (qty unit)` plus any note.
An optional item is lined up with the required item it replaces, found in this order: the *Replaces* choice set on the
examination page, otherwise the consumable's alternatives. Optional items that match neither are listed in an extra
column so nothing is lost. The *Full data workbook* is still available on the same page.

## Removing a consumable from many examinations

A consumable that is on examinations cannot be deleted (only deactivated). In the consumables list, *Remove from exams*
(needs the delete permission) opens a page showing how many examinations use it per modality. Remove it from all
modalities or just one, optionally deleting the consumable afterwards if nothing else uses it.

## Alternatives and saved sets

- **Alternative items:** the Add/Edit consumable form has an *Alternative items* section. Type a name; existing
  consumables and the Excel list are suggested, and a name that does not exist yet is created as a new
  consumable. Alternatives show under the consumable on examination pages (and in the Excel export); people who
  may change entries get a *use instead* button that swaps it in, keeping quantity and unit.
- **Saved sets:** Consumables > Saved sets. Build a named list independently (or press *Save as set* on an
  examination), then apply it from an examination page or in bulk (*Apply to many examinations*).
  Applying copies the items, so later edits to a set do not alter examinations that already used it.
- While typing a name, text that is not in the Excel list turns red with a message; it is still accepted and
  saved exactly as typed.

## Consumables list from Excel (auto-fill)

Consumables > Import list from Excel loads a workbook of consumables (columns Name, Category, Unit,
Description, Alternatives (several separated by `;`); common header spellings such as Item, Type, UOM, Notes are recognised). Download the template
from that page. When someone adds a consumable and types a name, matching items are suggested; picking one
fills in the category, unit and description. Items already added are shown greyed out. Importing again
updates, never deletes. A file named `Consumables_List.xlsx` placed in `data/` is loaded automatically at
start and whenever it changes. CLI: `python manage.py import_consumable_catalog file.xlsx [--add]`.

## Speeding up data entry

- **Copy from another examination:** on an examination page, or while adding a new examination
  ("Copy consumables from"). Existing consumables on the target are kept.
- **Apply to many examinations:** Consumables > Apply to many examinations, or tick rows on an examination
  list and press "Apply consumables to selected". Pick examinations (filter by modality or name), then copy a
  finished list from one examination, add consumables from dropdowns, or both. Choose whether existing
  consumables are left alone or replaced.
- **New modality on the fly:** the Add examination form has an "Or create a new modality" box.
  Rename, recolour, reorder or delete modalities under Setup > Modalities.

## Export

Export to Excel (sidebar, or the button on any examination list) downloads a workbook with sheets
Modalities, Examination consumables (one row per consumable per examination) and Consumables.
You can limit it to one modality and include inactive examinations or examinations without consumables.

## Rules

- A consumable linked to any examination is never hard-deleted (database-level PROTECT);
  "Retire" deactivates it instead. Inactive consumables stay on existing examinations
  but cannot be added to new ones.
- Deactivating hides an examination and keeps its data. Delete is offered only for inactive examinations and
  also removes their consumable assignments. The dashboard counts active examinations only.
- A modality can be deleted only while it has no examinations.

## Tests

    python manage.py test

## Before using it on a shared network

Settings are development defaults: set a real SECRET_KEY, DEBUG=False and ALLOWED_HOSTS in
config/settings.py, and put the app behind a login (it currently has none).
