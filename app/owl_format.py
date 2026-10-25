"""Write an ontology in the data repo's canonical layout before it is committed.

owlready2 writes subjects in the order it numbered them on load, so a
one-synonym publish could move ~1,100 lines (ARI#105), and Protégé and scripts
each write their own layout again. The data repo keeps every ``ontologies/*.owl``
in the one layout ROBOT writes (KrishnaTO/ARI ``tools/format-owl.sh``), and its
``OWL format`` check rejects anything else. Running the same pinned ROBOT here
makes the same content the same bytes, so the commit is the curator's change
alone.
"""
import subprocess
import tempfile
from pathlib import Path

from . import config


def format_owl(content: bytes) -> bytes:
    """``content`` re-laid-out by ROBOT ``convert``; the ontology itself is unchanged.

    Imports resolve through the ``catalog-v001.xml`` beside the runtime ontology,
    which deploy/update-ontology.sh fetches with it. Slow (~10 s for the full
    ontology): call it off the event loop.
    """
    catalog = Path(config.ONTOLOGY_FILE).parent / "catalog-v001.xml"
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in.owl", Path(tmp) / "out.owl"
        src.write_bytes(content)
        result = subprocess.run(
            ["java", "-jar", config.ROBOT_JAR, "convert", "--catalog", str(catalog),
             "--input", str(src), "--format", "owl", "--output", str(out)],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ROBOT could not format the ontology: {result.stderr.strip()}")
        return out.read_bytes()
