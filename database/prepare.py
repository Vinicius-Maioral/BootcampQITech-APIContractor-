"""Apply database.sql and verify against ORM metadata in one transaction. No migrations."""
from pathlib import Path
import re
import sys

from sqlalchemy import create_engine, inspect, text, CheckConstraint

sys.path.insert(0, '/app')
from constants import DATABASE_URL
from models.base import Base
import models  # noqa: F401: register the contract tables


def check_tokens(expression):
    expression = re.sub(r'::(?:character varying|text|numeric|integer)(?:\[\])?', '', expression, flags=re.I)
    expression = re.sub(r'\bIN\s*\(', '= (', expression, flags=re.I)
    expression = re.sub(r'\b(?:ANY|ARRAY)\b', '', expression, flags=re.I)
    return sorted(re.findall(r'[a-z_][a-z_0-9]*|[0-9]+|>=|>|=', expression.lower()))


def verify(connection):
    inspector = inspect(connection)
    errors = []
    for name, table in Base.metadata.tables.items():
        if not inspector.has_table(name):
            errors.append(f'{name}: missing table')
            continue
        actual = {c['name']: c for c in inspector.get_columns(name)}
        for column in table.columns:
            c = actual.get(column.name)
            if c is None:
                errors.append(f'{name}.{column.name}: missing column')
                continue
            expected_type = column.type.compile(dialect=connection.dialect)
            actual_type = c['type'].compile(dialect=connection.dialect)
            if expected_type != actual_type or c['nullable'] != column.nullable:
                errors.append(f'{name}.{column.name}: expected {expected_type} nullable={column.nullable}, got {actual_type} nullable={c["nullable"]}')
            if column.server_default is not None and 'now()' not in (c.get('default') or '').lower():
                errors.append(f'{name}.{column.name}: missing NOW() default')
        if inspector.get_pk_constraint(name)['constrained_columns'] != [c.name for c in table.primary_key]:
            errors.append(f'{name}: invalid primary key')
        uniques = {tuple(c['column_names']) for c in inspector.get_unique_constraints(name)}
        from sqlalchemy import UniqueConstraint
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint) and tuple(c.name for c in constraint.columns) not in uniques:
                errors.append(f'{name}: missing unique constraint {constraint}')
        foreign_keys = {(tuple(f['constrained_columns']), f['referred_table'], tuple(f['referred_columns']))
                        for f in inspector.get_foreign_keys(name)}
        for constraint in table.foreign_key_constraints:
            expected = (tuple(c.name for c in constraint.columns), constraint.referred_table.name,
                        tuple(e.column.name for e in constraint.elements))
            if expected not in foreign_keys:
                errors.append(f'{name}: missing foreign key {expected}')
        actual_checks = [check_tokens(c['sqltext']) for c in inspector.get_check_constraints(name)]
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint) and check_tokens(str(constraint.sqltext)) not in actual_checks:
                errors.append(f'{name}: missing check {constraint.sqltext}')
    for table, values in [('contract_status', {'draft', 'active', 'terminated'}),
                          ('contract_role', {'tenant', 'landlord', 'administrator', 'provider'})]:
        if inspector.has_table(table):
            found = set(connection.execute(text(f'SELECT enumerator FROM {table}')).scalars())
            if found != values:
                errors.append(f'{table}: unexpected enumerators {found}')
    if inspector.has_table('contract_party'):
        indexes = inspector.get_indexes('contract_party')
        if not any(i['name'] == 'contract_party_by_party' and i['column_names'] == ['party_key'] for i in indexes):
            errors.append('contract_party: missing party_key index')
    if inspector.has_table('contract_status_event'):
        triggers = connection.execute(text('''
            SELECT t.tgname, t.tgtype, t.tgenabled, p.proname FROM pg_trigger t
            JOIN pg_proc p ON p.oid=t.tgfoid WHERE t.tgrelid='contract_status_event'::regclass
        ''')).all()
        for name, mask in [('contract_status_event_no_change', 27), ('contract_status_event_no_truncate', 34)]:
            if not any(t[0] == name and t[1] == mask and t[2] in ('O', 'A') and t[3] == 'append_only_guard' for t in triggers):
                errors.append(f'contract_status_event: invalid/missing trigger {name}')
    if errors:
        raise RuntimeError('Schema incompatible; transaction rolled back:\n' + '\n'.join(errors))


if __name__ == '__main__':
    engine = create_engine(DATABASE_URL)
    try:
        with engine.begin() as connection:
            if '--verify-only' not in sys.argv:
                sql = Path(__file__).with_name('database.sql').read_text(encoding='utf-8')
                with connection.connection.cursor() as cursor:
                    cursor.execute(sql)
            verify(connection)
        print('PASS: database.sql, seven tables, types, constraints, seeds, index and append-only triggers')
    finally:
        engine.dispose()
