"""Research service coordinating web search and model API calls for agents."""

import asyncio
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.logging import get_logger
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


async def research_with_web_search(
    system_prompt: str,
    user_prompt: str,
    model: str = "gpt-4o",
) -> dict[str, Any]:
    """Run a research task that includes web search capabilities.

    Uses OpenAI Responses API with web_search_preview tool.
    Returns both the text response and any URLs cited.
    """
    client = get_openai_client()

    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await client.responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=messages,
        )

        output_text = ""
        urls_cited: list[dict[str, str]] = []

        for item in response.output:
            if hasattr(item, "content"):
                for content_block in item.content:
                    if hasattr(content_block, "text"):
                        output_text += content_block.text
                    if hasattr(content_block, "url"):
                        urls_cited.append({
                            "url": content_block.url,
                            "title": getattr(content_block, "title", ""),
                        })

        logger.info(
            "Research with web search completed",
            extra={"model": model, "urls_found": len(urls_cited)},
        )

        return {
            "text": output_text,
            "urls_cited": urls_cited,
        }

    except Exception as e:
        logger.error("Research with web search failed", extra={"error": str(e)})
        raise


async def research_with_structured_output(
    system_prompt: str,
    user_prompt: str,
    output_schema: type[T],
    model: str = "gpt-4o",
    max_retries: int = 1,
) -> T:
    """Run a research task and parse the result into a Pydantic model.

    Uses OpenAI structured outputs (response_format) for reliable parsing.
    Retries once on validation failure.
    """
    client = get_openai_client()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error: Exception | None = None

    for attempt in range(1 + max_retries):
        try:
            response = await client.responses.parse(
                model=model,
                input=messages,
                text_format=output_schema,
            )

            parsed = response.output_parsed
            if parsed is None:
                raise ValueError("Model returned no parseable output")

            return parsed

        except Exception as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(
                    "Structured output parse failed, retrying",
                    extra={"attempt": attempt + 1, "error": str(e)},
                )
                # Add a hint for the retry
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt + "\n\nIMPORTANT: Output MUST be valid JSON matching the required schema exactly. Do not truncate."},
                ]
            else:
                logger.error(
                    "Structured output parse failed after retries",
                    extra={"error": str(e)},
                )

    raise last_error  # type: ignore[misc]


async def generate_research(
    system_prompt: str,
    user_prompt: str,
    model: str = "gpt-4o",
    response_format: dict | None = None,
) -> str:
    """Call the model API with a system prompt and user query.

    Returns the model's text response.
    """
    client = get_openai_client()

    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        kwargs: dict[str, Any] = {
            "model": model,
            "input": messages,
        }

        if response_format:
            kwargs["text"] = {"format": response_format}

        response = await client.responses.create(**kwargs)

        output_text = ""
        for item in response.output:
            if hasattr(item, "content"):
                for content_block in item.content:
                    if hasattr(content_block, "text"):
                        output_text += content_block.text

        return output_text

    except Exception as e:
        logger.error("Research generation failed", extra={"error": str(e)})
        raise
