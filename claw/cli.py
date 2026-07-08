"""Command-line entry point for the Step 0 LLM smoke test."""

from __future__ import annotations

import sys

from claw.config import load_llm_config
from claw.errors import ClawError
from claw.llm import LLMClient


DEFAULT_PROMPT = "你好，请用一句话介绍你自己。"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    prompt = " ".join(args).strip() or DEFAULT_PROMPT

    try:
        config = load_llm_config()
        client = LLMClient(config)
        reply = client.chat([{"role": "user", "content": prompt}])
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130
    except ClawError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

