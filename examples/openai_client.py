"""Send one synthetic prompt through aliasgate with the OpenAI SDK, whole and then streamed.

    uv run --with openai python examples/openai_client.py

Environment:
    OPENAI_API_KEY   your provider key; the gateway forwards it unchanged
    OPENAI_MODEL     the model, or the deployment name on Azure OpenAI
    OPENAI_BASE_URL  the gateway (default http://localhost:8080/v1)

The provider sees placeholders such as [EMAIL_1]; this script prints the restored answers.
With the example configuration, the email and the IBAN are found by rules and
"ACME Corporation" by the example dictionary.
"""

import os
import sys

import openai

PROMPT = (
    "Write a two-sentence email to juan.perez@example.com confirming that the ACME Corporation"
    " refund to IBAN ES91 2100 0418 4502 0005 1332 was sent today. Repeat the email address"
    " and the IBAN exactly."
)


def main() -> None:
    model = os.environ.get("OPENAI_MODEL")
    if not model:
        sys.exit("set OPENAI_MODEL to the model (or Azure deployment) to call")
    client = openai.OpenAI(
        base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:8080/v1"),
    )
    messages: list[openai.types.chat.ChatCompletionMessageParam] = [
        {"role": "user", "content": PROMPT}
    ]
    try:
        raw = client.chat.completions.with_raw_response.create(
            model=model, messages=messages, max_completion_tokens=300
        )
        completion = raw.parse()
        print(f"entities pseudonymised: {raw.headers.get('x-aliasgate-entities')}")
        print(completion.choices[0].message.content)
        if completion.usage:
            print(
                f"({completion.usage.prompt_tokens} in, {completion.usage.completion_tokens} out)"
            )

        print("\nstreamed:")
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            max_completion_tokens=300,
            stream=True,
            stream_options={"include_usage": True},
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                print(chunk.choices[0].delta.content, end="", flush=True)
            if chunk.usage:  # the last chunk, which has no choices
                print(f"\n({chunk.usage.prompt_tokens} in, {chunk.usage.completion_tokens} out)")
    except openai.APIStatusError as exc:
        # Errors from the gateway itself carry its code, e.g. unsupported_content.
        sys.exit(f"error {exc.status_code}: {exc.message}")
    except openai.APIConnectionError:
        sys.exit("cannot reach the gateway; is `aliasgate serve` running?")


if __name__ == "__main__":
    main()
