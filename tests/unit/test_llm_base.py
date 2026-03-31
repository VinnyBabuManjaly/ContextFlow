"""Tests for the abstract LLM interface.

Verifies that the base class enforces the provider contract:
- All providers must implement complete()
- Message dataclass has the expected fields
"""

from contextflow.llm.base import LLMProvider, Message


class TestInterfaceRequiresCompleteMethod:
    """Subclassing LLMProvider without implementing complete() must raise TypeError."""

    def test_cannot_instantiate_without_complete(self) -> None:
        class IncompleteProvider(LLMProvider):
            pass

        try:
            IncompleteProvider()
            assert False, "Should have raised TypeError"
        except TypeError:
            pass


class TestMessageDataclassFields:
    """Message must have role and content fields."""

    def test_has_role_and_content(self) -> None:
        msg = Message(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_role_is_required(self) -> None:
        try:
            Message(content="hello")  # type: ignore[call-arg]
            assert False, "Should have raised TypeError"
        except TypeError:
            pass
