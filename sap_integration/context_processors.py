from django.conf import settings


def sap_feature(request):
    return {
        "sap_enabled": settings.SAP_ENABLED or settings.SAP_GM_IMPORT_ENABLED,
        "sap_gm_import_enabled": settings.SAP_GM_IMPORT_ENABLED,
    }
