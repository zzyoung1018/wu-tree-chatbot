"""Canonical administrative names shared by evidence checks and statistics."""

DISTRICT_ALIASES = {
    name: (name, name[:-1]) for name in
    ('吴中区', '姑苏区', '吴江区', '相城区', '昆山市', '常熟市', '太仓市', '张家港市')
}
DISTRICT_ALIASES.update({
    '虎丘区（高新区）': ('虎丘区（高新区）', '虎丘区', '高新区', '苏州高新区'),
    '工业园区': ('工业园区', '苏州工业园区', '园区'),
})


def normalize_district(name):
    return next((d for d, aliases in DISTRICT_ALIASES.items() if name in aliases), name)


def supports_district(candidate, requested):
    evidence = candidate.get('context_header', '') + '\n' + candidate['text']
    return (not requested or candidate['district'] in requested
            or any(a in evidence for d in requested for a in DISTRICT_ALIASES.get(d, (d,))))
