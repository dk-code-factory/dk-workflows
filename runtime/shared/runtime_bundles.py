"""Retain native plugin resources using the self-contained lifecycle implementation."""
import importlib.util
import re
from contextlib import contextmanager
from pathlib import Path

_API = None


def updater():
    global _API
    if _API is None:
        path = Path(__file__).resolve().parents[2] / 'scripts/update.py'
        spec = importlib.util.spec_from_file_location('dk_retention_updater', path)
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        _API = api
    return _API


def retain(root):
    return updater().retain_runtime(Path(root))


def verify(root):
    root = Path(root).resolve()
    _, state = updater().paths(root, Path.cwd())
    if root.parent != (state / 'runtimes').resolve():
        raise ValueError('Retained runtime must stay in the configured private store')
    return updater().verify_runtime(root)


def retained_binding(resolved):
    if 'root' not in resolved:
        return retained_command(resolved)
    original = Path(resolved['root'])
    root = retain(original)
    result = dict(resolved, root=str(root),
                  procedure=str(root / Path(resolved['procedure']).relative_to(original)),
                  origin_root=resolved.get('origin_root', str(original)), bundle_digest=verify(root))
    return result


def verify_binding(value):
    if 'bundle_digest' in value and verify(value['root']) != value['bundle_digest']:
        raise ValueError('retained provider runtime changed')
    for bundle in value.get('bundles', []):
        if verify(bundle['root']) != bundle['digest']:
            raise ValueError('retained command runtime changed')


def is_interpreter(command):
    name = Path(command).name.lower().removesuffix('.exe')
    return bool(re.fullmatch(r'(?:python|ruby|perl)(?:[0-9]+(?:\.[0-9]+)*)?', name)
                or name in ('node', 'nodejs', 'pwsh', 'powershell', 'sh', 'bash', 'zsh'))


def script_argument(arguments):
    """Identify an interpreter's script argument, without rewriting its data/output arguments."""
    if not is_interpreter(arguments[0]):
        return None
    name = Path(arguments[0]).name.lower().removesuffix('.exe')
    if name.startswith('python'):
        stops, values = {'-c', '-m'}, {'-W', '-X', '--check-hash-based-pycs'}
    elif name in ('node', 'nodejs'):
        stops = {'-e', '--eval', '-p', '--print'}
        values = {'-r', '--require', '--loader', '--import', '--input-type', '--conditions'}
    elif name in ('sh', 'bash', 'zsh'):
        stops, values = {'-c', '-s'}, {'-o', '-O', '--rcfile', '--init-file'}
    elif name in ('pwsh', 'powershell'):
        stops = {'-command', '-c', '-encodedcommand', '-encodedarguments'}
        values = {'-executionpolicy', '-inputformat', '-outputformat', '-workingdirectory', '-windowstyle'}
    else:
        stops = {'-e', '-E'} if name.startswith('perl') else {'-e'}
        values = {'-r', '-I', '-C', '-M', '-m'}
    index = 1
    while index < len(arguments):
        arg = arguments[index]
        option = arg.lower() if name in ('pwsh', 'powershell') else arg
        if option in stops:
            return None
        if option in values:
            index += 2
        elif arg == '--':
            return index + 1 if index + 1 < len(arguments) else None
        elif arg.startswith('-'):
            index += 1
        else:
            return index
    return None


def retained_command(resolved):
    import os
    import shutil
    import tempfile
    if resolved.get('bundles'):
        verify_binding(resolved)
        return resolved
    arguments = resolved['binding']['command']
    mapped = list(arguments)
    bundles = {}
    loose = {}
    entrypoint = script_argument(arguments)
    interpreter = is_interpreter(arguments[0])
    for index, argument in enumerate(arguments):
        prefix = ''
        candidate = argument
        if index == 0:
            candidate = shutil.which(argument) or argument
        elif argument.startswith('-'):
            if '=' not in argument:
                continue
            prefix, candidate = argument.split('=', 1)
            prefix += '='
        path = Path(candidate)
        if not path.is_absolute() or not path.exists():
            continue
        if index == 0 and interpreter:
            mapped[0] = str(path.resolve())
        package = next((folder for folder in [path if path.is_dir() else path.parent, *path.parents]
                        if any((folder / f'.{host}-plugin/plugin.json').is_file()
                               for host in ('codex', 'claude'))), None)
        if package is not None:
            retained = retain(package)
            bundles[str(retained)] = {'root': str(retained), 'digest': verify(retained)}
            mapped[index] = prefix + str(retained / path.relative_to(package))
        elif (index == entrypoint or (index == 0 and not interpreter)) and path.is_file():
            loose[index] = (prefix, path)
    if loose:
        own = Path(__file__).resolve().parents[2]
        _, state = updater().paths(own, Path.cwd())
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        common = Path(os.path.commonpath([str(path.parent) for _, path in loose.values()]))
        with tempfile.TemporaryDirectory(prefix='.command-', dir=state) as temporary:
            stage = Path(temporary)
            for _, path in loose.values():
                target = stage / 'resources' / path.relative_to(common)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                shutil.copymode(path, target)
                if updater().resource_hash(path) != updater().resource_hash(target):
                    raise ValueError('declared command resource changed while retaining it')
            retained = retain(stage)
        bundles[str(retained)] = {'root': str(retained), 'digest': verify(retained)}
        for index, (prefix, path) in loose.items():
            mapped[index] = prefix + str(retained / 'resources' / path.relative_to(common))
    return dict(resolved, argv=mapped, bundles=list(bundles.values())) if bundles else resolved



@contextmanager
def provider_scope(binding):
    from shared import bindings
    resolved = retained_binding(bindings.resolve(binding))
    execution = resolved if resolved.get('bundle_digest') or resolved.get('bundles') else None
    with bindings.pinned_bindings({bindings.key(binding): {'execution': execution}}):
        yield
