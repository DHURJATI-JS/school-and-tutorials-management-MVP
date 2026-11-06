"""Admin panel. Every route requires an admin session and CSRF protection on unsafe methods."""
from fastapi import APIRouter, Depends

from ...security import csrf_protect, require_admin
from . import (academics, classes, comms, dashboard, finance, insights, knowledge, loadtest, people, reports, security,
               site, tests)

router = APIRouter(dependencies=[Depends(require_admin), Depends(csrf_protect)])
for module in (dashboard, site, people, academics, classes, tests, knowledge, finance, comms, insights, reports,
               security, loadtest):
    router.include_router(module.router, prefix="/admin")
