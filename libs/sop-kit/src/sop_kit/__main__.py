"""Validate a knowledge library from the command line: python -m sop_kit <directory>"""

import sys

from sop_kit.chunker import chunk_document
from sop_kit.parser import SopParseError, load_library
from sop_kit.validator import validate_library


def main(argv: list[str]) -> int:
    directory = argv[0] if argv else "knowledge"
    try:
        docs = load_library(directory)
    except SopParseError as exc:
        print(f"PARSE ERROR {exc}")
        return 1
    report = validate_library(docs)
    for doc in docs:
        status = "FAIL" if doc.doc_id in report else "ok  "
        detail = f"{len(doc.steps)} steps" if doc.steps else f"{len(doc.sections)} sections"
        print(f"[{status}] {doc.doc_id:<12} {detail:<12} {len(chunk_document(doc)):>3} chunks"
              f"  {doc.title}")
        for problem in report.get(doc.doc_id, []):
            print(f"         - {problem}")
    print(f"\n{len(docs)} documents, {len(report)} with problems")
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
