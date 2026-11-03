"""기존 PostgreSQL 기록을 새 SQLite 파일로 한 번만 복사하는 관리 도구.

일반 앱 실행에서는 이 모듈과 psycopg를 사용하지 않는다.
DATABASE_URL 환경 변수로 원본을 지정하고 앱을 멈춘 뒤 실행한다.
"""

import argparse
import ipaddress
import json
import os
from datetime import date, datetime
from pathlib import Path

from roombot import db

TABLES = (
    ('daou', 'rooms'), ('daou', 'employees'), ('daou', 'holidays'),
    ('daou', 'reservations'), ('daou', 'reservation_attendees'),
    ('bot', 'slack_users'), ('bot', 'room_qr_tokens'), ('bot', 'checkins'),
    ('bot', 'noshow_cancellations'), ('bot', 'room_channels'),
    ('bot', 'slack_images'), ('bot', 'system_settings'), ('bot', 'notification_log'),
    ('bot', 'admin_blocks'), ('bot', 'admin_cancellations'),
)


def value_for_sqlite(value):
    if isinstance(value, datetime):
        return db.stamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address, ipaddress.IPv4Interface, ipaddress.IPv6Interface)):
        return str(value)
    return value


def migrate(source_url: str, destination: Path) -> dict[str, int]:
    import psycopg  # 일회성 이관 도구에서만 필요
    from psycopg import sql
    from psycopg.rows import dict_row

    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    counts = {}
    try:
        db.use_database(destination)
        db.initialize()
        with psycopg.connect(source_url, row_factory=dict_row) as source, db.tx(write=True) as target:
            source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            for schema, table in TABLES:
                rows = source.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(schema, table))).fetchall()
                columns = [r['name'] for r in target.execute(f'PRAGMA table_info({table})').fetchall()]
                copied = []
                for row in rows:
                    row = {('legacy_synced_at' if k == 'synced_to_daou_at' else k): value_for_sqlite(v)
                           for k, v in row.items()}
                    if set(row) != set(columns):
                        raise RuntimeError(f'원본 스키마가 다릅니다: {table}')
                    values = tuple(row[c] for c in columns)
                    target.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', values)
                    copied.append(values)
                actual = target.execute(f'SELECT * FROM {table}').fetchall()
                actual = [tuple(value_for_sqlite(row[c]) for c in columns) for row in actual]
                canonical = lambda records: sorted(json.dumps(row, ensure_ascii=False) for row in records)
                if canonical(copied) != canonical(actual):
                    raise RuntimeError(f'기록 대조 실패: {table}')
                counts[table] = len(rows)
                # 지운 예약의 번호도 재사용하지 않도록 원본 sequence 상한을 보존한다.
                if table in ('rooms', 'employees', 'reservations', 'checkins', 'notification_log'):
                    sequence = source.execute('SELECT pg_get_serial_sequence(%s, %s) AS name',
                                              (f'{schema}.{table}', 'id')).fetchone()['name']
                    if sequence:
                        seq = source.execute(sql.SQL('SELECT last_value FROM {}').format(
                            sql.Identifier(*sequence.split('.')))).fetchone()['last_value']
                        target.execute('INSERT INTO sqlite_sequence(name,seq) SELECT ?,? WHERE NOT EXISTS '
                                       '(SELECT 1 FROM sqlite_sequence WHERE name=?)', (table, seq, table))
                        target.execute('UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name=?', (seq, table))
            if target.execute('PRAGMA foreign_key_check').fetchall():
                raise RuntimeError('외래키 검증 실패')
            if target.execute('PRAGMA integrity_check').fetchone()['integrity_check'] != 'ok':
                raise RuntimeError('SQLite 무결성 검증 실패')
        return counts
    except BaseException:
        db._ready.discard(destination)
        for suffix in ('', '-wal', '-shm'):
            Path(str(destination) + suffix).unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = os.getenv('DATABASE_URL')
    if not source:
        parser.error('일회성 이관 원본 DATABASE_URL을 환경 변수로 지정하세요.')
    print(json.dumps(migrate(source, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
