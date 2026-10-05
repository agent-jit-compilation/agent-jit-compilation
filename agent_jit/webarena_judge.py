"""Original WebArena fallback judge prompt with explicit request accounting."""

from .browser_model import BrowserModel


async def judge_answer(agent_output, reference_answers, task_intent, ledger):
    # Format reference answers for the prompt
    ref_str = ""
    for match_type, value in reference_answers.items():
        if match_type == "exact_match":
            ref_str += f"Expected answer (exact): {value}\n"
        elif match_type == "must_include":
            if isinstance(value, list):
                ref_str += f"Must include all of: {', '.join(value)}\n"
            else:
                ref_str += f"Must include: {value}\n"
        elif match_type == "fuzzy_match":
            ref_str += f"Expected answer (fuzzy): {value}\n"

    prompt = f"""You are evaluating whether an AI agent correctly answered a task.

Task: {task_intent}

Reference Answer:
{ref_str}

Agent's Response:
{agent_output}

Does the agent's response contain the correct answer? The agent may provide additional context or explanation, but the key answer should match the reference.

Respond with:
- "CORRECT" if the agent's answer matches the reference (semantically equivalent)
- "INCORRECT" if the agent's answer does not match or is wrong

Then provide a brief explanation.

Format your response as:
VERDICT: [CORRECT/INCORRECT]
REASON: [brief explanation]"""

    client = BrowserModel("gpt-4.1-mini", ledger, max_output_tokens=200, temperature=0)
    try:
        response = await client.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        result = response.choices[0].message.content.strip()
        correct = "VERDICT: CORRECT" in result.upper()
        reason = (
            result.split("REASON:", 1)[-1].strip()
            if "REASON:" in result.upper()
            else result
        )
        return {"correct": correct, "score": float(correct), "reason": reason}
    finally:
        await client.close()
