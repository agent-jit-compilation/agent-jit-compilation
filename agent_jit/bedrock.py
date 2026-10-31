"""Amazon Bedrock chat model for budgeted planner and browser requests."""

import asyncio
from dataclasses import dataclass
from functools import cached_property

from browser_use.llm.aws.chat_bedrock import ChatAWSBedrock
from browser_use.llm.aws.serializer import AWSBedrockMessageSerializer
from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage


@dataclass
class BedrockChat(ChatAWSBedrock):
    """ChatAWSBedrock with a forced structured-output tool call, run off the event loop.

    The pinned base class calls boto3 synchronously inside ainvoke, which serializes
    parallel candidate generation, and it lets the model answer in text when a
    structured output is required.
    """

    def _get_usage(self, response):
        # Bedrock reports cached prompt tokens separately; count them as input at the full rate.
        usage = response.get("usage")
        if not usage:
            return None
        cached = usage.get("cacheReadInputTokens") or 0
        written = usage.get("cacheWriteInputTokens") or 0
        return ChatInvokeUsage(
            prompt_tokens=usage.get("inputTokens", 0) + cached + written,
            prompt_cached_tokens=cached,
            prompt_cache_creation_tokens=written,
            prompt_image_tokens=None,
            completion_tokens=usage.get("outputTokens", 0),
            total_tokens=usage.get("totalTokens", 0) + cached + written,
        )

    @cached_property
    def client(self):
        # One client per model instance; creating boto3 clients across threads is unsafe.
        return self._get_client()

    async def ainvoke(self, messages, output_format=None):
        from botocore.exceptions import ClientError

        bedrock_messages, system = AWSBedrockMessageSerializer.serialize_messages(messages)
        request = {"modelId": self.model, "messages": bedrock_messages}
        if system:
            request["system"] = system
        if inference := self._get_inference_config():
            request["inferenceConfig"] = inference
        if output_format is not None:
            request["toolConfig"] = {
                "tools": self._format_tools_for_request(output_format),
                "toolChoice": {"any": {}},
            }
        try:
            response = await asyncio.to_thread(self.client.converse, **request)
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 502)
            raise ModelProviderError(str(error), status_code=status, model=self.name) from error
        usage = self._get_usage(response)
        content = response.get("output", {}).get("message", {}).get("content", [])
        if output_format is None:
            text = "\n".join(item["text"] for item in content if "text" in item)
            return ChatInvokeCompletion(completion=text, usage=usage)
        for item in content:
            if "toolUse" in item:
                return ChatInvokeCompletion(
                    completion=output_format.model_validate(item["toolUse"].get("input", {})),
                    usage=usage,
                )
        raise ModelProviderError(
            "Expected structured output but no tool use found in response", model=self.name
        )
