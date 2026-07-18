"""Tests for DialogueType enum validation."""

import pytest
from pydantic import ValidationError

from app.models.podcast import DialogueType, ScriptSegment


class TestDialogueType:
    """Tests for the DialogueType enum in ScriptSegment."""

    def test_advice_is_accepted(self):
        """The 'advice' dialogue type should be valid."""
        segment = ScriptSegment(
            segment_id="seg-01",
            speaker="photographer",
            dialogue="I'd recommend arriving 30 minutes before sunset.",
            finding_ids=["f-01"],
            dialogue_type="advice",
        )
        assert segment.dialogue_type == DialogueType.ADVICE
        assert segment.dialogue_type.value == "advice"

    def test_all_valid_types_accepted(self):
        """All enum values should be accepted."""
        valid_types = [
            "observation", "fact", "story", "question",
            "response", "transition", "intro", "outro", "advice",
        ]
        for dtype in valid_types:
            segment = ScriptSegment(
                segment_id="seg-01",
                speaker="historian",
                dialogue="Test dialogue.",
                dialogue_type=dtype,
            )
            assert segment.dialogue_type == dtype

    def test_unsupported_type_is_rejected(self):
        """An unsupported dialogue type should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ScriptSegment(
                segment_id="seg-01",
                speaker="photographer",
                dialogue="Test dialogue.",
                dialogue_type="monologue",
            )
        assert "dialogue_type" in str(exc_info.value)

    def test_empty_type_is_rejected(self):
        """An empty string should raise ValidationError."""
        with pytest.raises(ValidationError):
            ScriptSegment(
                segment_id="seg-01",
                speaker="photographer",
                dialogue="Test dialogue.",
                dialogue_type="",
            )

    def test_default_type_is_observation(self):
        """Default dialogue_type should be 'observation'."""
        segment = ScriptSegment(
            segment_id="seg-01",
            speaker="photographer",
            dialogue="Test dialogue.",
        )
        assert segment.dialogue_type == DialogueType.OBSERVATION
