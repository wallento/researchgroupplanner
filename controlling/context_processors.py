from django.conf import settings


def config_settings(request):
    return {
        "OVERHEAD_SPLIT_ENABLED": settings.OVERHEAD_SPLIT_ENABLED,
        "LANDESSTELLEN_ENABLED": settings.LANDESSTELLEN_ENABLED,
        "ANNUAL_POOLS_ENABLED": settings.ANNUAL_POOLS_ENABLED,
        "BRAND_LOGO": settings.BRAND_LOGO,
    }
