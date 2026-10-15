"""The Propose prompt: its system guidance, and the source lines it lists."""

import pytest
from fakes import FakeLLM

from cyris.domain.models import SourceConfig
from cyris.service_layer import area_llm
from cyris.service_layer.prompts import PROPOSE_AREAS_GUIDANCE

pytestmark = pytest.mark.unit

REPLY_SHAPE = '{"<area name>": ["<source name>", ...]}'


async def test_the_system_prompt_carries_the_guidance_and_ends_with_the_reply_shape() -> None:
    llm = FakeLLM("{}")
    sources = {"A": SourceConfig(name="A", url="https://a.example/feed", tags=["ai"])}

    await area_llm.propose_areas(llm, sources)

    system = llm.calls[0]["system"]
    assert PROPOSE_AREAS_GUIDANCE in system
    assert system.rstrip().endswith(REPLY_SHAPE)


def test_the_guidance_is_at_least_two_sentences() -> None:
    sentences = [s for s in PROPOSE_AREAS_GUIDANCE.split(".") if s.strip()]

    assert len(sentences) >= 2
