from django.urls import path
from django.contrib.auth.views import LogoutView

from . import accounts as a
from . import set_views as sv
from . import views as v

urlpatterns = [
    path('login/', a.ThrottledLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('setup/', a.setup, name='setup'),
    path('profile/', a.profile, name='profile'),
    path('users/', a.user_list, name='user_list'),
    path('users/add/', a.user_create, name='user_create'),
    path('users/<int:pk>/edit/', a.user_edit, name='user_edit'),
    path('users/<int:pk>/password/', a.user_password, name='user_password'),
    path('consumables/suggest/', v.consumable_suggest, name='consumable_suggest'),
    path('consumables/import/', v.catalog_import, name='catalog_import'),
    path('consumables/import/template.xlsx', v.catalog_template, name='catalog_template'),

    path('', v.dashboard, name='dashboard'),

    path('examinations/', v.exam_list, name='exam_list'),
    path('examinations/add/', v.exam_create, name='exam_create'),
    path('examinations/modality/<slug:slug>/', v.exam_list, name='modality_exam_list'),
    path('examinations/<int:pk>/', v.exam_detail, name='exam_detail'),
    path('examinations/<int:pk>/edit/', v.exam_edit, name='exam_edit'),
    path('examinations/<int:pk>/toggle/', v.exam_toggle, name='exam_toggle'),
    path('examinations/<int:pk>/save/', v.exam_save_all, name='exam_save_all'),
    path('examinations/<int:pk>/consumables/add/', v.link_add, name='link_add'),
    path('examinations/<int:pk>/consumables/copy/', v.exam_copy, name='exam_copy'),
    path('examinations/<int:pk>/delete/', v.exam_delete, name='exam_delete'),
    path('examinations/bulk-apply/', v.bulk_apply, name='bulk_apply'),
    path('exam-consumables/<int:pk>/update/', v.link_update, name='link_update'),
    path('exam-consumables/<int:pk>/remove/', v.link_remove, name='link_remove'),
    path('exam-consumables/<int:pk>/swap/', v.link_swap, name='link_swap'),
    path('examinations/<int:pk>/apply-set/', sv.exam_apply_set, name='exam_apply_set'),
    path('examinations/<int:pk>/save-as-set/', sv.exam_save_as_set, name='exam_save_as_set'),

    path('sets/', sv.set_list, name='set_list'),
    path('sets/add/', sv.set_create, name='set_create'),
    path('sets/<int:pk>/', sv.set_detail, name='set_detail'),
    path('sets/<int:pk>/edit/', sv.set_edit, name='set_edit'),
    path('sets/<int:pk>/toggle/', sv.set_toggle, name='set_toggle'),
    path('sets/<int:pk>/delete/', sv.set_delete, name='set_delete'),
    path('sets/<int:pk>/save/', sv.set_save_all, name='set_save_all'),
    path('sets/<int:pk>/items/add/', sv.set_item_add, name='set_item_add'),
    path('set-items/<int:pk>/update/', sv.set_item_update, name='set_item_update'),
    path('set-items/<int:pk>/remove/', sv.set_item_remove, name='set_item_remove'),

    path('consumables/', v.consumable_list, name='consumable_list'),
    path('consumables/add/', v.consumable_create, name='consumable_create'),
    path('consumables/<int:pk>/edit/', v.consumable_edit, name='consumable_edit'),
    path('consumables/<int:pk>/toggle/', v.consumable_toggle, name='consumable_toggle'),
    path('consumables/<int:pk>/delete/', v.consumable_delete, name='consumable_delete'),
    path('consumables/<int:pk>/usage/', v.consumable_usage, name='consumable_usage'),
    path('consumables/<int:pk>/remove-everywhere/', v.consumable_remove_everywhere, name='consumable_remove_everywhere'),

    path('setup/categories/', v.category_list, name='category_list'),
    path('setup/categories/<int:pk>/delete/', v.category_delete, name='category_delete'),
    path('setup/modalities/', v.modality_list, name='modality_list'),
    path('setup/modalities/<int:pk>/edit/', v.modality_edit, name='modality_edit'),
    path('setup/modalities/<int:pk>/delete/', v.modality_delete, name='modality_delete'),
    path('export/', v.export_excel, name='export_excel'),
    path('setup/units/', v.unit_list, name='unit_list'),
    path('setup/units/<int:pk>/edit/', v.unit_edit, name='unit_edit'),
    path('setup/units/<int:pk>/toggle/', v.unit_toggle, name='unit_toggle'),
]
