"""Check wheel-only dependency resolution across core Python targets.

Usage: python scripts/check_core_wheels.py [--requirements PATH] [--python VER ...] [--target TRIPLE ...] [--json]
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

TARGETS = {
    'x86_64-pc-windows-msvc': 'Windows x64',
    'aarch64-apple-darwin': 'macOS Apple silicon',
    'x86_64-apple-darwin': 'macOS Intel',
    'x86_64-manylinux_2_28': 'Linux x64',
    'aarch64-manylinux_2_28': 'Linux ARM64',
}
PYTHON_VERSIONS = ('3.11', '3.12', '3.13', '3.14', '3.15')
DEFAULT_REQUIREMENTS = Path(__file__).resolve().parents[1] / 'requirements-core.txt'
UV_TIMEOUT_SECONDS = 120
STDERR_TAIL_LINES = 5


def parse_args(argv):
    def python_version(value):
        if not re.fullmatch(r'[0-9]+\.[0-9]+', value):
            raise argparse.ArgumentTypeError('Python version must be digits.digits')
        return value

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requirements', type=Path, default=DEFAULT_REQUIREMENTS)
    parser.add_argument('--python', action='append', type=python_version)
    parser.add_argument('--target', action='append', choices=list(TARGETS))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    args.python = list(dict.fromkeys(args.python or PYTHON_VERSIONS))
    args.target = list(dict.fromkeys(args.target or TARGETS))
    return args


def find_uv():
    path = shutil.which('uv')
    if path:
        return [path]
    command = [sys.executable, '-m', 'uv']
    try:
        result = subprocess.run(command + ['--version'], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return command if result.returncode == 0 else None


def compile_argv(uv, triple, version, manifest, binary_only):
    argv = uv + ['pip', 'compile', '--quiet', '--no-header',
                 '--python-platform', triple, '--python-version', version]
    if binary_only:
        argv += ['--only-binary', ':all:']
    return argv + [str(manifest)]


def run_uv(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=UV_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return 124, '', f'uv timed out after {UV_TIMEOUT_SECONDS} s'
    except OSError as error:
        return 127, '', f'could not start uv: {error}'
    return result.returncode, result.stdout, result.stderr


def parse_pin_line(line):
    line = line.strip()
    if not line or line.startswith(('#', '-')) or '==' not in line:
        return None
    name, version = line.split('==', 1)
    name = re.sub(r'\[[^\]]*\]', '', name).strip().lower()
    name = re.sub(r'[-_.]+', '-', name)
    version = re.split(r'[\s;]', version, maxsplit=1)[0]
    return name, version


def parse_pins(text):
    pins = {}
    for line in text.splitlines():
        pin = parse_pin_line(line)
        if pin is not None:
            pins[pin[0]] = pin[1]
    return pins


def stderr_tail(stderr, returncode):
    lines = [line for line in stderr.splitlines() if line.strip()]
    return '\n'.join(lines[-STDERR_TAIL_LINES:]) if lines else (
        f'uv exited with code {returncode} and printed nothing to stderr')


def version_warnings(wheel_pins, newest_pins):
    return [{'package': name, 'newest': newest_pins[name], 'wheel': wheel_pins[name]}
            for name in sorted(wheel_pins.keys() & newest_pins.keys())
            if wheel_pins[name] != newest_pins[name]]


def check_one(uv, triple, version, manifest):
    result = {'target': triple, 'label': TARGETS[triple], 'python': version,
              'status': 'fail', 'pins': {}, 'warnings': [], 'error': ''}
    code, stdout, stderr = run_uv(compile_argv(uv, triple, version, manifest, True))
    if code:
        result['error'] = stderr_tail(stderr, code)
        return result
    result['pins'] = parse_pins(stdout)
    code, stdout, stderr = run_uv(compile_argv(uv, triple, version, manifest, False))
    if code:
        result['status'] = 'warn'
        result['error'] = ('unrestricted resolve failed, versions not compared: '
                           + stderr_tail(stderr, code))
        return result
    result['warnings'] = version_warnings(result['pins'], parse_pins(stdout))
    result['status'] = 'warn' if result['warnings'] else 'ok'
    return result


def format_line(result):
    line = (f"{result['status'].upper():<4}  {result['label']} "
            f"({result['target']}), Python {result['python']}")
    details = [f"{warning['package']}: newest {warning['newest']} has no wheel here; "
               f"installers pass --prefer-binary, so {warning['wheel']} installs"
               for warning in result['warnings']]
    details.extend(result['error'].splitlines())
    return line + (': ' + ' | '.join(details) if details else '')


def main(argv=None):
    args = parse_args(argv)
    if not args.requirements.is_file():
        print(f'requirements file not found: {args.requirements}', file=sys.stderr)
        return 2
    uv = find_uv()
    if uv is None:
        print(f'uv not found on PATH or as {sys.executable} -m uv; run: pip install uv',
              file=sys.stderr)
        return 2
    manifest = args.requirements.resolve()
    results = [check_one(uv, target, version, manifest)
               for target in args.target for version in args.python]
    summary = {status: sum(result['status'] == status for result in results)
               for status in ('ok', 'warn', 'fail')}
    if args.json:
        print(json.dumps({'requirements': str(manifest), 'results': results,
                          'summary': summary}, indent=2))
    else:
        for result in results:
            print(format_line(result))
        print(f"Summary: {summary['ok']} ok, {summary['warn']} warn, {summary['fail']} fail")
    return 1 if summary['fail'] else 0


if __name__ == '__main__':
    sys.exit(main())
