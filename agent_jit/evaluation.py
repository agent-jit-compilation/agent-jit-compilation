"""Shared task evaluators; output parsing mode is an explicit experiment choice."""

import importlib
import json
import math
from numbers import Real
import sys
from .budget import Ledger, BudgetedModel


def custom_verifier(verifier, root=None):
    """Resolve a trusted local verifier before spending on a trial."""
    name = verifier.get("callable", "")
    parts = name.split(":") if isinstance(name, str) else []
    if len(parts) != 2 or not all(parts):
        raise ValueError("Custom verifier requires callable: module:function")
    if root is not None:
        sys.path.insert(0, str(root))
    try:
        try:
            function = getattr(importlib.import_module(parts[0]), parts[1])
        except (ImportError, AttributeError) as exc:
            raise ValueError(
                f"Custom verifier {name} cannot be imported: {exc}"
            ) from exc
        if not callable(function):
            raise ValueError(f"Custom verifier {name} is not callable")
        return function
    finally:
        if root is not None:
            sys.path.pop(0)


async def evaluate(
    task,
    text,
    final_state,
    mode,
    ledger_path=None,
    budget=1,
    max_requests=128,
    parser_model="gpt-4.1-2025-04-14",
):
    verifier = task["verifier"]
    if verifier["type"] == "custom":
        import inspect

        result = custom_verifier(verifier)(task, text, final_state)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict) or type(result.get("correct")) is not bool:
            raise ValueError(
                "Custom verifier must return a dictionary with boolean correct"
            )
        value = result.get("score")
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise ValueError("Custom verifier score must be a finite number in [0, 1]")
        score = float(value)
        if result["correct"] and score != 1:
            raise ValueError("Custom verifier correct=True requires score=1")
        return {
            "correct": result["correct"],
            "score": score,
            "mode": verifier["callable"],
        }
    if task["benchmark"] == "WebArena":
        raise NotImplementedError(
            "WebArena requires its environment adapter and pinned evaluator checkout"
        )
    module = importlib.import_module(
        verifier["script"].removesuffix(".py").replace("/", ".")
    )
    if verifier["type"] == "state_check":
        if final_state is None:
            raise ValueError(
                "State evaluator requires /finish JSON; no output-only fallback"
            )
        correct, score, details = module.evaluate(final_state, text)
        return {
            "correct": bool(correct),
            "score": float(score),
            "details": details,
            "mode": "original_script",
        }
    validator = module.validator
    if mode == "historical":
        from browser_use.llm.messages import UserMessage
        from .native_model import NativeModel

        if ledger_path is None:
            raise ValueError("Historical evaluation requires a shared budget ledger")
        client = NativeModel(
            "claude-sonnet-4-20250514", Ledger(ledger_path, budget, max_requests), 8192
        )
        try:
            # Same prompt, model, provider serializer, and schema as TaskValidator.
            response = await client.ainvoke(
                [
                    UserMessage(
                        content=f"Convert the following text to the requested structured JSON format.\n\nText:\n{text}"
                    )
                ],
                output_format=validator.output_schema,
            )
            parsed = response.completion
            return {
                "correct": bool(validator.check_correctness(parsed)),
                "score": float(validator.check_correctness_pct(parsed)),
                "mode": "historical_claude_sonnet_4_20250514",
            }
        finally:
            await client.close()
    if mode == "json":
        parsed = validator.output_schema.model_validate_json(text)
    elif mode == "openai":
        from .model_catalog import provider

        if provider(parser_model) != "openai":
            raise ValueError(
                "Use evaluator: historical for the paper's Claude parser; openai mode requires an OpenAI parser model"
            )
        if ledger_path is None:
            raise ValueError("OpenAI evaluation requires a shared budget ledger")
        client = BudgetedModel(
            parser_model, Ledger(ledger_path, budget, max_requests), 1024
        )
        prompt = f"Convert the following text to the requested structured JSON format. Return only a JSON object.\nSchema: {json.dumps(validator.output_schema.model_json_schema())}\nText:\n{text}"
        try:
            stream = await client.create(
                model=client.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            chunks = []
            async for c in stream:
                if c.choices and c.choices[0].delta.content:
                    chunks.append(c.choices[0].delta.content)
            parsed = validator.output_schema.model_validate_json("".join(chunks))
        finally:
            await client.client.close()
    else:
        raise ValueError("Unknown evaluator mode")
    return {
        "correct": bool(validator.check_correctness(parsed)),
        "score": float(validator.check_correctness_pct(parsed)),
        "mode": "structured_json_no_parser"
        if mode == "json"
        else f"{parser_model}_structured_output_parser",
    }
