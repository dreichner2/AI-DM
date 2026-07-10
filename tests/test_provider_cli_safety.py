from __future__ import annotations

import pytest

from scripts import check_llm_provider, list_gemini_models


@pytest.mark.parametrize(
    ('argv', 'expected_code'),
    [
        (['--help'], 0),
        (['unexpected'], 2),
    ],
)
def test_check_llm_provider_argument_handling_is_side_effect_free(
    monkeypatch,
    argv,
    expected_code,
):
    def unexpected_call(*args, **kwargs):
        del args, kwargs
        raise AssertionError('argument handling must not load configuration or construct a provider')

    monkeypatch.setattr(check_llm_provider, 'load_runtime_env', unexpected_call)
    monkeypatch.setattr(check_llm_provider, 'get_provider', unexpected_call)

    with pytest.raises(SystemExit) as exc_info:
        check_llm_provider.main(argv)

    assert exc_info.value.code == expected_code


@pytest.mark.parametrize(
    ('argv', 'expected_code'),
    [
        (['--help'], 0),
        (['unexpected'], 2),
    ],
)
def test_list_gemini_models_argument_handling_is_side_effect_free(
    monkeypatch,
    argv,
    expected_code,
):
    def unexpected_call(*args, **kwargs):
        del args, kwargs
        raise AssertionError('argument handling must not load configuration or access Gemini')

    monkeypatch.setattr(list_gemini_models, 'load_runtime_env', unexpected_call)
    monkeypatch.setattr(list_gemini_models, '_list_generate_content_models', unexpected_call)

    with pytest.raises(SystemExit) as exc_info:
        list_gemini_models.main(argv)

    assert exc_info.value.code == expected_code
