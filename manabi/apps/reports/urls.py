from django.conf.urls import *
from django.conf import settings

from manabi.apps.utils.views import direct_to_template


urlpatterns = [
    url(r'^spring_break_usage$', 'manabi.apps.reports.views.spring_break_usage'),
]
