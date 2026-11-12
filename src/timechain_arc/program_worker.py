"""Restricted, bounded generated-policy evaluator; no benchmark imports."""
import ast
import builtins
import importlib
import json
import sys

MODULES = {'collections', 'itertools', 'functools', 'math', 'heapq', 'copy',
           'operator', 'statistics', 'fractions', 're', 'bisect'}
BUILTINS = {'abs', 'all', 'any', 'bool', 'dict', 'divmod', 'enumerate', 'filter',
            'float', 'frozenset', 'int', 'isinstance', 'issubclass', 'iter', 'len',
            'list', 'map', 'max', 'min', 'next', 'pow', 'range', 'repr', 'reversed',
            'round', 'set', 'slice', 'sorted', 'str', 'sum', 'tuple', 'zip',
            'Exception', 'ValueError', 'TypeError', 'StopIteration', 'KeyError',
            'IndexError', 'AssertionError', 'NotImplementedError'}


def validate(code):
    if len(code) > 200_000:
        raise ValueError('Program too large')
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.Global, ast.Nonlocal)):
            raise ValueError('Classes and global/nonlocal mutation are not supported')
        if isinstance(node, ast.Attribute) and node.attr.startswith('_'):
            raise ValueError('Private attributes are unavailable')
        if isinstance(node, ast.Name) and (node.id.startswith('__') or node.id in
                {'open', 'eval', 'exec', 'compile', 'getattr', 'setattr', 'delattr',
                 'globals', 'locals', 'vars', 'dir', 'input', 'breakpoint', 'help'}):
            raise ValueError('Introspection and external capabilities are unavailable')
        if isinstance(node, ast.Import):
            if any(n.name not in MODULES for n in node.names):
                raise ValueError('Module not allowed')
        if isinstance(node, ast.ImportFrom):
            if node.level or node.module not in MODULES or any(n.name.startswith('_') or n.name == '*' for n in node.names):
                raise ValueError('Import not allowed')
    return tree


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name not in MODULES or level:
        raise ImportError(name)
    return importlib.import_module(name)


def grid(value):
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 30:
        raise ValueError('Grid height outside 1..30')
    rows = [list(r) for r in value]
    if not rows or not 1 <= len(rows[0]) <= 30 or any(len(r) != len(rows[0]) for r in rows):
        raise ValueError('Nonrectangular grid')
    if any(type(c) is not int or not 0 <= c <= 9 for r in rows for c in r):
        raise ValueError('Invalid grid color')
    return rows


def grids(ns, inp):
    if 'candidates' in ns:
        values = []
        for i, v in enumerate(ns['candidates'](inp)):
            if i >= 64:
                break
            values.append(grid(v))
        return values
    return [grid(ns['solve'](inp))]


def execute(request):
    validate(request['code'])
    safe = {k: getattr(builtins, k) for k in BUILTINS}
    safe['__import__'] = safe_import
    ns = {'__builtins__': safe}
    exec(compile(request['code'], '<learned-policy>', 'exec'), ns)
    analysis = ns['analyze'](request['obs'], request['demos']) if 'analyze' in ns else None
    if 'solve' not in ns and 'candidates' not in ns and 'act' not in ns:
        if analysis is None:
            raise ValueError('Program provides no analyze, solve, candidates or act function')
        return {'action': None, 'state': request['state'], 'analysis': analysis, 'checked_demos': 0}
    if request['kind'] == 'grid':
        for pair in request['demos']:
            if grid(pair['output']) not in grids(ns, pair['input']):
                raise ValueError('Policy contradicts a purchased demonstration')
        candidates = grids(ns, request['obs']['input'])
        result = next((g for g in candidates if g not in request['tried']), None)
        return {'action': None if result is None else {'type': 'submit', 'grid': result},
                'state': {}, 'analysis': analysis, 'checked_demos': len(request['demos'])}
    state = request['state']
    action = ns['act'](request['obs'], state, request['demos'])
    if isinstance(action, list):
        action = {'type': 'act', 'id': action[0],
                  **({'x': action[1], 'y': action[2]} if len(action) == 3 else {})}
    return {'action': action, 'state': state, 'analysis': analysis, 'checked_demos': 0}


if __name__ == '__main__':
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
        resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
        resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024**2, 2 * 1024**2))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except ImportError:
        pass  # Windows engineering tests; verified runs require Linux/bwrap.
    try:
        result = execute(json.load(sys.stdin))
        encoded = json.dumps(result)
        if len(encoded) > 2 * 1024**2:
            raise ValueError('Policy reply too large')
        print(encoded)
    except Exception as error:
        print(json.dumps({'error': type(error).__name__ + ': ' + str(error)[:1000]}))
