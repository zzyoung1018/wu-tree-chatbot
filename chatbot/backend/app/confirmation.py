"""Apply the user's confirmation to application metadata, without editing sources."""
import json


def confirm_metadata(db):
    for table, key, field in (('entries', 'unit_id', 'card'), ('chunks', 'chunk_id', 'chunk')):
        rows = db.execute(f"SELECT {key},{field} FROM {table} WHERE review_status!='rejected'").fetchall()
        for identity, raw in rows:
            value = json.loads(raw)
            value['review_status'] = 'verified'
            for flag in value.get('review_flags', []):
                if flag['code'] == 'location_conflict':
                    # Preserve both recorded places without an added review request.
                    flag['note'] = flag['note'].replace('正文内部有冲突，暂照录标题，待馆方核实', '').rstrip('。') + '。'
            db.execute(f'UPDATE {table} SET review_status=?,{field}=? WHERE {key}=?',
                       ('verified', json.dumps(value, ensure_ascii=False), identity))
