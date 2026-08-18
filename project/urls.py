"""
URL configuration for screenplay project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import re

from django.contrib import admin
from django.conf import settings
from django.urls import path, re_path
from django.views.static import serve
from scene.views import LandingView


def _serve_dir(url, document_root):
    """Serve `document_root` at `url` the way static() does, but without its DEBUG check."""
    return [
        re_path(
            r"^%s(?P<path>.*)$" % re.escape(url.lstrip("/")),
            serve,
            {"document_root": document_root},
        )
    ]


urlpatterns = [
    path('', LandingView.as_view(), name='landing'),
    path('admin/', admin.site.urls),
]

if settings.SERVE_MEDIA:
    urlpatterns += _serve_dir(settings.MEDIA_URL, settings.MEDIA_ROOT)

# Under DEBUG the staticfiles app already serves these from the app directories,
# and it does it without needing collectstatic to have run.
if settings.SERVE_STATIC and not settings.DEBUG:
    urlpatterns += _serve_dir(settings.STATIC_URL, settings.STATIC_ROOT)
