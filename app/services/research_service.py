"""Research service coordinating web search and model API calls for agents."""

import asyncio
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)


async def web_search(query: str, num_results: int = 5) -> list[dict[str, Any]]:
    """Perform a web search using OpenAI's web search tool.

    Returns a list of search result dicts with url, title, and snippet.
    """
    client = get_openai_client()

    try:
        response = await client.responses.create(
            model="gpt-4o-mini",
            tools=[{"type": "web_search_preview"}],
            input=query,
        )

        results: list[dict[str, Any]] = []
        for item in response.output:
            if hasattr(item, "content"):
                for content_block in item.content:
                    if hasattr(content_block, "url"):
                        results.append({
                            "url": content_block.url,
                            "title": getattr(content_block, "title", ""),
                            "snippet": getattr(content_block, "text", ""),
                        })

        logger.info("Web search completed", extra={"query": query, "results_count": len(results)})
        return results[:num_results]

    except Exception as e:
        logger.error("Web search failed", extra={"query": query, "error": str(e)})
        raise


async def generate_research(
    system_prompt: str,
    user_prompt: str,
    model: str = "gpt-4o",
    response_format: dict | None = None,
) -> str:
    """Call the model API with a system prompt and user query for research generation.

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

        logger.info("Research generation completed", extra={"model": model})
        return output_text

    except Exception as e:
        logger.error("Research generation failed", extra={"error": str(e)})
        raise


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
