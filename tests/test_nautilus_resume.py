"""Offline checks for the cluster bootstrap and interrupted-run command selection."""

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = ROOT / 'scripts/nautilus_phase2_checkout.sh'
RUNLIB = ROOT / 'scripts/nautilus_phase2_runlib.sh'


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout


def source_repo(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'phase_2', str(source)], check=True)
    git(source, 'config', 'user.email', 'test@example.invalid')
    git(source, 'config', 'user.name', 'Test')
    (source / 'base.txt').write_text('base\n')
    git(source, 'add', 'base.txt')
    git(source, 'commit', '-qm', 'base')
    return source


def checkout(tmp_path, source, patch, resume='0'):
    env = {**os.environ, 'PHASE2_REPO_DIR': str(tmp_path / 'work' / 'checkout'),
           'PHASE2_SLUG': 'test', 'PHASE2_RESUME': resume,
           'PHASE2_PATCH_PATH': str(patch),
           'PHASE2_CHECKOUT_STAMP': str(tmp_path / 'work' / 'stamp.txt'),
           'PHASE2_REPO_URL': str(source)}
    return subprocess.run(['bash', str(CHECKOUT)], env=env, capture_output=True, text=True)


def test_empty_patch_starts_and_resumes_after_merge(tmp_path):
    source = source_repo(tmp_path)
    patch = tmp_path / 'empty.patch'
    patch.write_text('')
    first = checkout(tmp_path, source, patch)
    assert first.returncode == 0, first.stderr
    assert git(tmp_path / 'work' / 'checkout', 'status', '--porcelain') == ''
    assert checkout(tmp_path, source, patch, '1').returncode == 0
    assert 'checkout already exists' in checkout(tmp_path, source, patch).stderr


def test_patched_checkout_resumes_only_with_same_source(tmp_path):
    source = source_repo(tmp_path)
    (source / 'base.txt').write_text('changed\n')
    (source / 'added.txt').write_text('new\n')
    git(source, 'add', '-N', 'added.txt')
    patch = tmp_path / 'change.patch'
    patch.write_text(git(source, 'diff', '--binary', 'HEAD'))
    first = checkout(tmp_path, source, patch)
    assert first.returncode == 0, first.stderr
    saved = tmp_path / 'work' / 'checkout'
    assert (saved / 'added.txt').read_text() == 'new\n'
    assert checkout(tmp_path, source, patch, '1').returncode == 0
    patch.write_text(patch.read_text() + '\n')
    assert 'checkout or patch changed' in checkout(tmp_path, source, patch, '1').stderr


def runlib(tmp_path, resume, out):
    env = {**os.environ, 'PHASE2_RESUME': resume}
    command = 'python() { printf "%s\\n" "$@"; }; source "$1"; run_phase2_matrix "$2" --clean-only --methods none'
    return subprocess.run(['bash', '-c', command, '_', str(RUNLIB), str(out)],
                          env=env, capture_output=True, text=True)


def test_matrix_resume_uses_existing_metadata_only(tmp_path):
    out = tmp_path / 'matrix'
    fresh = runlib(tmp_path, '1', out)
    assert fresh.returncode == 0 and '--resume' not in fresh.stdout
    out.mkdir()
    (out / 'manifest.json').write_text('{}')
    partial = runlib(tmp_path, '1', out)
    assert partial.returncode != 0 and 'incomplete resume metadata' in partial.stderr
    (out / 'selection.json').write_text('{}')
    continued = runlib(tmp_path, '1', out)
    assert continued.returncode == 0 and continued.stdout.strip().endswith('--resume')
    assert '--resume' not in runlib(tmp_path, '0', out).stdout
