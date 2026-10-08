"""Validate our published record schema's used subset, with no third-party runtime dependency.

This is not a general JSON Schema implementation. Supported keywords are exactly
those used in production-record.schema.json; unknown keywords fail closed.
"""
from datetime import datetime
import json
import math
from pathlib import Path
import re


KEYWORDS = {'$schema', 'title', '$defs', '$ref', 'type', 'additionalProperties', 'required', 'properties',
            'const', 'enum', 'anyOf', 'oneOf', 'items', 'minItems', 'maxItems', 'uniqueItems', 'minLength', 'minimum', 'exclusiveMinimum', 'pattern', 'format'}


def validate(value, schema=None):
    if schema is None:
        schema = json.loads((Path(__file__).resolve().parents[1] / 'references/production-record.schema.json').read_text(encoding='utf-8'))

    def visit(data, rule, path):
        unknown = set(rule) - KEYWORDS
        if unknown:
            raise ValueError(path + ': unsupported schema keyword ' + ', '.join(sorted(unknown)))
        if '$ref' in rule:
            target = schema
            if not rule['$ref'].startswith('#/'):
                raise ValueError(path + ': only local schema references are supported')
            for key in rule['$ref'][2:].split('/'):
                target = target[key.replace('~1', '/').replace('~0', '~')]
            visit(data, target, path)
        for keyword in ('anyOf', 'oneOf'):
            if keyword not in rule:
                continue
            matched = 0
            for branch in rule[keyword]:
                try:
                    visit(data, branch, path)
                    matched += 1
                except ValueError:
                    pass
            if not matched or keyword == 'oneOf' and matched != 1:
                raise ValueError(path + ': does not match ' + keyword)
        kinds = rule.get('type', [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        matches = {'null': data is None, 'object': isinstance(data, dict), 'array': isinstance(data, list),
                   'string': isinstance(data, str), 'boolean': type(data) is bool,
                   'integer': type(data) is int, 'number': type(data) in (int, float) and math.isfinite(data)}
        if kinds and not any(matches[k] for k in kinds):
            raise ValueError(path + ': wrong type')
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if 'const' in rule and encoded != json.dumps(rule['const'], ensure_ascii=False, sort_keys=True):
            raise ValueError(path + ': wrong constant')
        if 'enum' in rule and encoded not in [json.dumps(x, ensure_ascii=False, sort_keys=True) for x in rule['enum']]:
            raise ValueError(path + ': outside enum')
        if isinstance(data, dict):
            missing = set(rule.get('required', [])) - set(data)
            if missing:
                raise ValueError(path + ': missing ' + ', '.join(sorted(missing)))
            properties = rule.get('properties', {})
            for key, item in data.items():
                extra = rule.get('additionalProperties', True)
                child = properties.get(key, extra)
                if child is False:
                    raise ValueError(path + ': unknown field ' + key)
                if isinstance(child, dict):
                    visit(item, child, path + '.' + key)
        if isinstance(data, list):
            if len(data) < rule.get('minItems', 0):
                raise ValueError(path + ': too few items')
            if 'maxItems' in rule and len(data) > rule['maxItems']:
                raise ValueError(path + ': too many items')
            if rule.get('uniqueItems') and len({json.dumps(x, sort_keys=True, ensure_ascii=False) for x in data}) != len(data):
                raise ValueError(path + ': duplicate items')
            if 'items' in rule:
                for index, item in enumerate(data):
                    visit(item, rule['items'], path + '[' + str(index) + ']')
        if isinstance(data, str):
            if len(data) < rule.get('minLength', 0) or 'pattern' in rule and not re.search(rule['pattern'], data):
                raise ValueError(path + ': invalid text')
            if rule.get('format') == 'date-time':
                try:
                    date = datetime.fromisoformat(data.replace('Z', '+00:00'))
                    if date.tzinfo is None:
                        raise ValueError('timezone required')
                except ValueError:
                    raise ValueError(path + ': invalid date-time')
        if type(data) in (int, float):
            if 'minimum' in rule and data < rule['minimum'] or 'exclusiveMinimum' in rule and data <= rule['exclusiveMinimum']:
                raise ValueError(path + ': below minimum')

    visit(value, schema, '$')
    return True
