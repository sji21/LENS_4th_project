"""LENS 관계형 DB와 Chroma 컬렉션을 초기화한다."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database import (  # noqa: E402
    initialize_relational_database,
    initialize_vector_store,
    resolve_database_paths,
)


def parse_args() -> argparse.Namespace:
    defaults = resolve_database_paths()
    parser = argparse.ArgumentParser(description="법령·판례 지식 DB를 초기화합니다.")
    parser.add_argument("--database", type=Path, default=defaults.relational)
    parser.add_argument("--chroma-path", type=Path, default=defaults.chroma)
    parser.add_argument("--collection", default="knowledge_chunks")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    relational = initialize_relational_database(args.database)
    vector = initialize_vector_store(
        args.chroma_path,
        collection_name=args.collection,
    )
    print(f"SQLite: {relational.path}")
    print(f"Schema version: {relational.schema_version}")
    print(f"Tables: {len(relational.tables)}")
    print(f"Chroma: {vector.path}")
    print(f"Collection: {vector.collection_name} ({vector.document_count} chunks)")


if __name__ == "__main__":
    main()
