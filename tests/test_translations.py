"""Translation, exception and icon files stay complete and consistent (#34).

Custom integrations ship ``translations/en.json`` as the source;
``strings.json`` is kept as an identical copy. These checks mirror the
parts of hassfest that matter here, so a gap fails locally first.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
from typing import Any

import pytest
import yaml

from custom_components.eg_austria.const import DOMAIN
from custom_components.eg_austria.importer import RunStatus
from custom_components.eg_austria.sensor import SENSORS

PKG = Path(__file__).parents[1] / "custom_components" / DOMAIN
LANGUAGES = ("en", "de")

# Raises of these classes reach the user and must be translated.
TRANSLATED_ERRORS = {
    "HomeAssistantError",
    "ServiceValidationError",
    "ConfigEntryAuthFailed",
    "ConfigEntryNotReady",
    "ConfigEntryError",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _translations(lang: str) -> dict[str, Any]:
    return _load(PKG / "translations" / f"{lang}.json")


def _leaves(node: Any, prefix: str = "") -> dict[str, str]:
    """Flatten to ``{"a.b.c": "text"}``."""

    if isinstance(node, dict):
        out: dict[str, str] = {}
        for key, value in node.items():
            out.update(_leaves(value, f"{prefix}.{key}" if prefix else key))
        return out
    return {prefix: node}


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


def test_strings_json_equals_en() -> None:
    """``strings.json`` is an exact copy of ``translations/en.json``."""

    assert _load(PKG / "strings.json") == _translations("en")


def test_languages_have_the_same_keys() -> None:
    """English and German define the same keys, data_description included."""

    en, de = (set(_leaves(_translations(lang))) for lang in LANGUAGES)
    assert sorted(en - de) == [], "missing in de.json"
    assert sorted(de - en) == [], "missing in en.json"


def test_languages_use_the_same_placeholders() -> None:
    """A placeholder in one language exists in the other."""

    en, de = (_leaves(_translations(lang)) for lang in LANGUAGES)
    diff = {
        k: (en[k], de[k]) for k in en if _placeholders(en[k]) != _placeholders(de[k])
    }
    assert diff == {}


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_field_has_a_data_description(lang: str) -> None:
    """Bronze ``config-flow``: each data key of each step is described."""

    strings = _translations(lang)
    for section in ("config", "options"):
        for name, step in strings[section]["step"].items():
            data = set(step.get("data", {}))
            described = set(step.get("data_description", {}))
            assert data == described, f"{lang} {section}.step.{name}"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_strings_follow_hassfest_rules(lang: str) -> None:
    """No URLs, HTML, key references or stray spaces; ``{name}`` placeholders."""

    for key, text in _leaves(_translations(lang)).items():
        assert isinstance(text, str), key
        assert text, key
        assert text == text.strip(), key
        assert not re.search(r"https?://", text), key
        assert not re.search(r"<[a-zA-Z/][^>]*>", text), key
        assert "[%key:" not in text, key
        # Every brace pair is a simple placeholder.
        assert re.sub(r"\{\w+\}", "", text).count("{") == 0, key
        assert re.sub(r"\{\w+\}", "", text).count("}") == 0, key


def _raises() -> list[tuple[str, int, ast.Call]]:
    found = []
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                continue
            func = node.exc.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in TRANSLATED_ERRORS:
                found.append((path.name, node.lineno, node.exc))
    return found


def test_user_facing_raises_are_translated() -> None:
    """Gold ``exception-translations``: no literal messages, known keys only."""

    raises = _raises()
    assert raises, "the scan found no raises; is the pattern stale?"
    exceptions = {lang: _translations(lang)["exceptions"] for lang in LANGUAGES}
    for module, line, call in raises:
        where = f"{module}:{line}"
        assert not call.args, f"{where} passes a literal message"
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        domain = kwargs.get("translation_domain")
        assert isinstance(domain, ast.Name), where
        assert domain.id == "DOMAIN", where
        key = kwargs.get("translation_key")
        assert isinstance(key, ast.Constant), where
        assert isinstance(key.value, str), where
        for lang in LANGUAGES:
            assert key.value in exceptions[lang], f"{where} {lang}: {key.value}"


def test_icons_match_entities_and_services() -> None:
    """Gold ``icon-translations``: icons refer to existing keys and states."""

    icons = _load(PKG / "icons.json")
    strings = _translations("en")

    sensor_keys = {d.translation_key for d in SENSORS}
    entity_icons = icons["entity"]["sensor"]
    assert set(entity_icons) <= sensor_keys
    assert set(entity_icons) <= set(strings["entity"]["sensor"])

    status = entity_icons["last_run_status"]
    assert "default" in status
    assert set(status["state"]) == {s.value for s in RunStatus}
    assert set(status["state"]) == set(
        strings["entity"]["sensor"]["last_run_status"]["state"]
    )

    services = yaml.safe_load((PKG / "services.yaml").read_text(encoding="utf-8"))
    assert set(icons["services"]) == set(services) == set(strings["services"])

    for key, icon in _leaves(icons).items():
        assert re.fullmatch(r"mdi:[a-z0-9-]+", icon), key
