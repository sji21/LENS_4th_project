import hashlib

import pytest

from scripts.patch021_verify import check_settings, committed_source


def setup_source(tmp_path, monkeypatch, *, dirty=False, changed=False):
    for rel in ('scripts/patch021_verify.py', 'src/retrieval/service.py'):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'value = 2\r\n' if changed else b'value = 1\r\n')

    def git(args):
        command = args[3:]
        if command[0] == 'status':
            return b' M src/retrieval/service.py' if dirty else b''
        if command[0] == 'rev-parse':
            return b'revision' if command[1] == 'HEAD' else b'tree'
        assert command[0] == 'show'
        return b'value = 1\n'
    monkeypatch.setattr('scripts.patch021_verify.subprocess.check_output', git)


def test_committed_source_accepts_only_newline_difference(tmp_path, monkeypatch):
    setup_source(tmp_path, monkeypatch)
    result = committed_source(tmp_path)
    assert result['commit'] == 'revision'
    assert result['status'] == ''
    source = result['sources']['src/retrieval/service.py']
    assert source['working_sha256'] != source['git_sha256']
    assert source['lf_sha256'] == hashlib.sha256(b'value = 1\n').hexdigest()


def test_committed_source_rejects_dirty_tree(tmp_path, monkeypatch):
    setup_source(tmp_path, monkeypatch, dirty=True)
    with pytest.raises(ValueError, match='clean committed tree'):
        committed_source(tmp_path)


def test_committed_source_rejects_hidden_content_change(tmp_path, monkeypatch):
    setup_source(tmp_path, monkeypatch, changed=True)
    with pytest.raises(ValueError, match='Source differs'):
        committed_source(tmp_path)


def test_settings_separate_new_civil_budget_from_legacy():
    old = {'search_k': {'k_law': 5, 'k_case': 5, 'k_guide': 2}, 'corpora': {}}
    current = {**old, 'search_k': {**old['search_k'], 'k_civil': 3}}
    assert check_settings(current, old) == {
        'legacy_settings_unchanged': True, 'added_search_k': {'k_civil': 3}}
    assert current['search_k']['k_civil'] == 3
    for key, value in [('k_civil', 2), ('k_civil', None), ('k_law', 4)]:
        invalid = {**current, 'search_k': {**current['search_k'], key: value}}
        with pytest.raises(ValueError, match='Unexpected search settings'):
            check_settings(invalid, old)
