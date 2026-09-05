"""
OpenAI/Gemini client wrapper for PrepCampus Coach.
Handles API calls with error handling and response parsing.
"""

import os
import json
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod


class AIClient(ABC):
    """Abstract base class for AI clients."""

    @abstractmethod
    def generate_response(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        """Generate a response from the AI model."""
        pass

    @abstractmethod
    def generate_structured_response(
        self,
        system_prompt: str,
        user_message: str,
        output_schema: Dict[str, Any],
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """Generate a structured JSON response from the AI model."""
        pass


class OpenAIClient(AIClient):
    """OpenAI API client for PrepCampus Coach."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4"):
        """
        Initialize OpenAI client.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: Model to use (default: gpt-4)
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model

        if not self.api_key:
            raise ValueError("OpenAI API key not provided or found in environment")

        try:
            import openai
            openai.api_key = self.api_key
            self.client = openai.OpenAI(api_key=self.api_key)
        except ImportError:
            raise ImportError("openai package not installed. Run: pip install openai")

    def generate_response(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        """
        Generate a text response from OpenAI.

        Args:
            system_prompt: System prompt for the model
            user_message: User message
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in response

        Returns:
            Generated response text
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            raise RuntimeError(f"OpenAI API error: {str(e)}")

    def generate_structured_response(
        self,
        system_prompt: str,
        user_message: str,
        output_schema: Dict[str, Any],
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """
        Generate a structured JSON response from OpenAI using function calling.

        Args:
            system_prompt: System prompt for the model
            user_message: User message
            output_schema: JSON schema for the output
            temperature: Sampling temperature

        Returns:
            Structured response as dictionary
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature,
                functions=[
                    {
                        "name": "analyze_student",
                        "description": "Analyze student performance and provide coaching",
                        "parameters": output_schema,
                    }
                ],
                function_call={"name": "analyze_student"},
            )

            # Extract function call arguments
            function_args = response.choices[0].message.function_call.arguments
            return json.loads(function_args)
        except Exception as e:
            raise RuntimeError(f"OpenAI structured response error: {str(e)}")


class GeminiClient(AIClient):
    """Google Gemini client using the supported google.genai SDK."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.5-flash"):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.model = model

        if not self.api_key:
            raise ValueError("Gemini API key not provided or found in environment")

        try:
            from google import genai
            from google.genai import types
            self._types = types
            self.client = genai.Client(api_key=self.api_key)
        except ImportError:
            raise ImportError(
                "google-genai package not installed. Run: pip install google-genai"
            )

    def _model_name(self) -> str:
        return str(self.model or "").strip().removeprefix("models/")

    def generate_response(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        full_message = f"{system_prompt}\n\n{user_message}"
        response = self.client.models.generate_content(
            model=self._model_name(),
            contents=full_message,
            config=self._types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        return (response.text or "").strip()

    def generate_structured_response(
        self,
        system_prompt: str,
        user_message: str,
        output_schema: Dict[str, Any],
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        json_schema = json.dumps(output_schema, indent=2)
        full_message = f"""{system_prompt}

{user_message}

IMPORTANT: Return your response as valid JSON matching this schema:
{json_schema}

Return ONLY the JSON object, no other text."""

        response = self.client.models.generate_content(
            model=self._model_name(),
            contents=full_message,
            config=self._types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=4000,
                response_mime_type="application/json",
                response_schema=output_schema,
            ),
        )
        response_text = (response.text or "").strip()
        if "```json" in response_text:
            response_text = response_text.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in response_text:
            response_text = response_text.split("```", 1)[1].split("```", 1)[0]
        return json.loads(response_text.strip())

def create_client(
    provider: str = "openai", api_key: Optional[str] = None, model: Optional[str] = None
) -> AIClient:
    """
    Factory function to create an AI client.

    Args:
        provider: "openai" or "gemini"
        api_key: API key for the provider
        model: Model name (optional)

    Returns:
        AIClient instance

    Raises:
        ValueError: If provider is not supported
    """
    if provider.lower() == "openai":
        return OpenAIClient(api_key=api_key, model=model or "gpt-4")
    elif provider.lower() == "gemini":
        return GeminiClient(api_key=api_key, model=model or "gemini-pro")
    else:
        raise ValueError(f"Unsupported provider: {provider}. Use 'openai' or 'gemini'")
