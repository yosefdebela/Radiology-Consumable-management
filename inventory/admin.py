from django.contrib import admin
from . import models

admin.site.register(models.Modality)
admin.site.register(models.Unit)
admin.site.register(models.ConsumableCategory)


class LinkInline(admin.TabularInline):
    model = models.ExaminationConsumable
    extra = 0


@admin.register(models.Examination)
class ExaminationAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'modality', 'is_active')
    list_filter = ('modality', 'is_active')
    search_fields = ('name', 'code')
    inlines = [LinkInline]


@admin.register(models.Consumable)
class ConsumableAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'default_unit', 'is_active')
    list_filter = ('category', 'is_active')
    search_fields = ('name',)
