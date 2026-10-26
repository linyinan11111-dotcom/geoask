"""Local profile round-trip, native encryption, provider boundaries and failures."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
import geoask as g
import geoask_credentials as credentials


def save(key='test-only-local-secret', llm=None):
    llm = llm or g.LLMSettings()
    credentials.save_connection(llm.model_dump(), g.MapSettings().model_dump(),
                                g.RoutingSettings().model_dump(), key)
    return llm


def test_roundtrip_and_clear():
    llm = save()
    assert credentials.saved_api_key(llm.model_dump()) == 'test-only-local-secret'
    assert credentials.load_connection()['llm']['model'] == llm.model
    if credentials._WINDOWS:
        assert b'test-only-local-secret' not in credentials.connection_file().read_bytes()
    credentials.clear_connection()
    credentials.clear_connection()
    assert credentials.load_connection() is None


def test_new_process_can_decrypt_same_user_profile():
    llm = save()
    code = "import sys; from pathlib import Path; import geoask_credentials as c; c.connection_file=lambda:Path(sys.argv[1]); p=c.load_connection(); assert p['api_key']=='test-only-local-secret'; print('restored')"
    result = subprocess.run([sys.executable, '-c', code, str(credentials.connection_file())], cwd=g.ROOT,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0 and result.stdout.strip() == 'restored'
    assert credentials.saved_api_key(llm.model_dump())


@pytest.mark.parametrize('change', [dict(base_url='https://other.example/v1'),
                                  dict(base_url='https://api.deepseek.com/other'),
                                  dict(api_key_env='OTHER_API_KEY')])
def test_saved_key_not_reused_for_other_providers(change):
    llm = save()
    assert not credentials.saved_api_key(llm.model_copy(update=change).model_dump())
    assert credentials.saved_api_key(llm.model_copy(update={'base_url':llm.base_url+'/'}).model_dump())


def test_environment_override_and_local_fallback(monkeypatch):
    llm = save()
    monkeypatch.delenv(llm.api_key_env, raising=False)
    assert g.resolve_api_key(llm) == 'test-only-local-secret'
    monkeypatch.setenv(llm.api_key_env, 'environment-secret')
    assert g.resolve_api_key(llm) == 'environment-secret'
    credentials.connection_file().write_bytes(b'broken')
    assert g.resolve_api_key(llm) == 'environment-secret'
    monkeypatch.delenv(llm.api_key_env)
    with pytest.raises(g.GeoaskError, match='无法读取本地'):
        g.resolve_api_key(llm)


@pytest.mark.parametrize('content', [b'', b'broken', b'GEOASK-JSON-1\n{}', b'X'*131073],
                         ids=['empty','invalid-header','invalid-schema','oversized'])
def test_corrupt_profile_reports_no_content(content):
    path = credentials.connection_file()
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    with pytest.raises(credentials.LocalSettingsError, match='本地接口设置') as error:
        credentials.load_connection()
    assert 'broken' not in str(error.value)


def test_failed_save_keeps_previous_profile(monkeypatch):
    save()
    previous = credentials.connection_file().read_bytes()
    monkeypatch.setattr(os, 'replace', MagicMock(side_effect=PermissionError('private-body')))
    with pytest.raises(credentials.LocalSettingsError, match='无法保存') as error:
        save('replacement-secret')
    assert 'replacement-secret' not in str(error.value) and 'private-body' not in str(error.value)
    assert credentials.connection_file().read_bytes() == previous


def test_posix_fallback_restricts_permissions(monkeypatch):
    monkeypatch.setattr(credentials, '_WINDOWS', False)
    save()
    path = credentials.connection_file()
    assert path.read_bytes().startswith(b'GEOASK-JSON-1\n')
    if os.name != 'nt':
        assert path.stat().st_mode & 0o777 == 0o600
    assert credentials.load_connection()['api_key'] == 'test-only-local-secret'


def test_saved_key_used_by_both_parsers_without_environment(monkeypatch):
    llm = save()
    monkeypatch.delenv(llm.api_key_env, raising=False)
    factory = MagicMock()
    monkeypatch.setattr(g, 'OpenAI', factory)
    client = factory.return_value.__enter__.return_value
    message = NS(content='{"intent":"咖啡","modifiers":[]}', refusal=None)
    client.chat.completions.create.return_value = NS(choices=[NS(finish_reason='stop', message=message)])
    assert g.parse_intent('咖啡', llm).intent == '咖啡'
    assert factory.call_args.kwargs['api_key'] == 'test-only-local-secret'
    message.content = '{"stops":[{"intent":"咖啡","modifiers":[]}],"travel_mode":"walking"}'
    assert len(g.parse_plan('咖啡', llm).stops) == 1
    assert factory.call_args.kwargs['api_key'] == 'test-only-local-secret'
