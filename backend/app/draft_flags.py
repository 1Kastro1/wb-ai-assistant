"""Small database-maintained projections for dashboard reads, never publication approval."""
from sqlalchemy.sql.expression import ColumnElement
from sqlalchemy.ext.compiler import compiles
from sqlalchemy import Boolean


def flag_sql(name, dialect):
    def value(path):
        keys = path.split('.')
        if dialect == 'postgresql':
            return "(quality #>> '{" + ','.join(keys) + "}')"
        return "json_extract(quality, '$." + path + "')"
    def truth(path, default='false'):
        raw = value(path)
        if dialect == 'postgresql':
            raw = '(' + raw + ')::boolean'
        return f'coalesce({raw}, {default})'
    passed = value('passed')
    if dialect == 'postgresql':
        passed = '(' + passed + ')::boolean'
    technical = f"(not {truth('owner_rules.passed')} and ({truth('owner_rules.technical_error')} or coalesce({value('owner_rules.status')}, '') = 'error'))"
    pending = f"({technical} or coalesce({value('owner_rules.status')}, '') = 'pending' or not {truth('owner_rules.checked', 'true')})"
    defects = f"(coalesce({value('text_issues')}, '[]') <> '[]' or coalesce({value('owner_rules.violations')}, '[]') <> '[]')"
    fallback = f"(model LIKE 'safety-fallback%' and coalesce({value('repair_audit.rules_rechecked_at')}, '') = '')"
    return {'passed': passed, 'technical': technical, 'pending': pending,
            'attention': f'coalesce(({defects} or (not {pending} and ({passed} = false or {fallback}))), false)'}[name]


class DraftFlag(ColumnElement):
    type = Boolean()
    inherit_cache = False

    def __init__(self, name):
        self.name = name


@compiles(DraftFlag)
def compile_flag(element, compiler, **kw):
    return flag_sql(element.name, compiler.dialect.name)
