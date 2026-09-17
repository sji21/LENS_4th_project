"""Compatibility entry point used by setup_data.py for the selected Git corpus."""
from src.ingestion.knowledge_release import (
    CASE_COUNT as EXPECTED_CASE_COUNT,
    create_release,
    file_hash,
    main,
    read_release,
    verify_release,
    write_runtime_profile,
)

if __name__ == "__main__":
    raise SystemExit(main())
