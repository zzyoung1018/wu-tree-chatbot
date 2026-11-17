"""Runtime place lookups. Geographic parsing lives in the offline builder."""


def area_stem(value):
    value = value.strip()
    for suffix in ('古镇', '街道', '镇', '乡'):
        if value.endswith(suffix):
            return value[:-len(suffix)]
    return value


def fallback_enrichment(cards, chunks, database_sha256, glossary):
    # Compatibility for old test fixtures/snapshots only, never the production path.
    from .offline_enrichment import base_enrichment
    return base_enrichment(cards, chunks, glossary, database_sha256)
