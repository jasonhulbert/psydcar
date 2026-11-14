"""Export the versioned app API contract for the Angular build."""

import json

from ..store import SidecarStore
from . import create_app


print(json.dumps(create_app(SidecarStore(), 8765, watch=False).openapi()))
