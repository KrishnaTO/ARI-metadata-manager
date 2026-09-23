"""HTTP routes, grouped by the part of the app they serve.

``ROUTERS`` is the order ``app.main`` includes them in. Every path is distinct,
so the order is presentational — except that the HTML pages must be registered
before the StaticFiles mount, which ``app.main`` does last.
"""
from . import assignments, auth, feedback, merge, ontology, pages, publish, review, settings

ROUTERS = [
    ontology.router,
    review.router,
    feedback.router,
    auth.router,
    publish.router,
    merge.router,
    settings.router,
    assignments.router,
    pages.router,
]
