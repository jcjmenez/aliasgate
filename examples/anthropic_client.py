"""Send one synthetic prompt through aliasgate with the Anthropic SDK, whole and then streamed.

    uv run --with anthropic python examples/anthropic_client.py

Environment:
    ANTHROPIC_API_KEY   your provider key; the gateway forwards it unchanged
    ANTHROPIC_MODEL     the model (default claude-opus-5-5), or the deployment name on Foundry
    ANTHROPIC_BASE_URL  the gateway (default http://localhost:8080)

The provider sees placeholders such as [EMAIL_1]; this script prints the restored answers.
With the example configuration, the email and the IBAN are found by rules and
"ACME Corporation" by the example dictionary.
"""

import os
import sys

import anthropic

PROMPT = (
    "Write a two-sentence email to juan.perez@example.com confirming that the ACME Corporation"
    " refund to IBAN ES91 2100 0418 4502 0005 1332 was sent today. Repeat the email address"
    " and the IBAN exactly."
)


def main() -> None:
    model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
    client = anthropic.Anthropic(
        base_url=os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:8080"),
    )
    messages: list[anthropic.types.MessageParam] = [{"role": "user", "content": PROMPT}]
    try:
        # A short answer needs little thinking; max_tokens also bounds the thinking.
        raw = client.messages.with_raw_response.create(
            model=model, max_tokens=300, output_config={"effort": "low"}, messages=messages
        )
        message = raw.parse()
        print(f"entities pseudonymised: {raw.headers.get('x-aliasgate-entities')}")
        print("".join(block.text for block in message.content if block.type == "text"))
        print(f"({message.usage.input_tokens} in, {message.usage.output_tokens} out)")

        print("\nstreamed:")
        with client.messages.stream(
            model=model, max_tokens=300, output_config={"effort": "low"}, messages=messages
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
            usage = stream.get_final_message().usage
        print(f"\n({usage.input_tokens} in, {usage.output_tokens} out)")
    except anthropic.APIStatusError as exc:
        # Errors from the gateway itself carry its code, e.g. unsupported_content.
        sys.exit(f"error {exc.status_code}: {exc.message}")
    except anthropic.APIConnectionError:
        sys.exit("cannot reach the gateway; is `aliasgate serve` running?")


if __name__ == "__main__":
    main()
