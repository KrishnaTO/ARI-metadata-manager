"""Process-wide ledgers, instantiated once.

Both are durable on-disk stores that outlive any one request and are read from
more than one route module, so they are created here rather than owned by
whichever route happened to need them first.
"""
from . import assignment_service, config, id_provenance

# Per-curator disease assignments and their done markers.
ASSIGNMENTS = assignment_service.AssignmentStore(config.ASSIGN_DIR)

# Who added each cross-reference id — the review page's separation of duties.
ID_AUTHORS = id_provenance.IdAuthorStore(config.PROVENANCE_DIR)
