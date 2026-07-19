from django.conf import settings


def sap_feature(request):
    return {"sap_enabled": settings.SAP_ENABLED}
