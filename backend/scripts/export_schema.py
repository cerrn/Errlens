"""Writes the JSON Schema of the contract models to frontend/lib/schema.json.

Frontend types are generated from that file:  cd frontend && npm run gen:types
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from pydantic.json_schema import models_json_schema  # noqa: E402

from app import models  # noqa: E402

EXPORTED = [
    models.AnalyzeRequest,
    models.AnalysisResult,
    models.StepEvent,
    models.HistoryItem,
    models.HistoryList,
    models.ErrorResponse,
    models.HealthResponse,
]


def _strip_field_titles(node, top=False):
    """Drops field titles, which would produce noisy TS type aliases (model titles are kept)."""
    if isinstance(node, dict):
        if not top and "properties" not in node and "enum" not in node:
            node.pop("title", None)
        for v in node.values():
            _strip_field_titles(v)
    elif isinstance(node, list):
        for v in node:
            _strip_field_titles(v)


def main() -> None:
    # AnalyzeRequest is an input (optional fields stay optional); everything else is output
    modes = [(m, "validation" if m is models.AnalyzeRequest else "serialization") for m in EXPORTED]
    _, schema = models_json_schema(modes, ref_template="#/$defs/{model}")
    for d in schema["$defs"].values():
        for prop in d.get("properties", {}).values():
            _strip_field_titles(prop, top=False)
            prop.pop("title", None)
    schema["title"] = "ErrLensContract"
    # Reference every model from the root so json-schema-to-typescript emits all types
    schema["type"] = "object"
    schema["properties"] = {m.__name__: {"$ref": f"#/$defs/{m.__name__}"} for m in EXPORTED}
    schema["additionalProperties"] = False
    out = ROOT / "frontend" / "lib" / "schema.json"
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
