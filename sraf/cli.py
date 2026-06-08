from __future__ import annotations

import argparse
from pathlib import Path
import sys

from dotenv import load_dotenv

from sraf.conversation import ConversationSession
from sraf.escalation import CliEscalation
from sraf.evaluator import Evaluator
from sraf.instructions import InstructionExtractor, InstructionValidator
from sraf.llm import GigaChatClient, ScriptedLLMClient
from sraf.meta_loop import MetaLoop
from sraf.prompts import BASE_SYSTEM_PROMPT
from sraf.refiner import PromptRefiner
from sraf.runner import AgentRunner
from sraf.sanitizer import PromptSanitizer
from sraf.sandbox import DockerPythonSandbox, RestrictedSubprocessSandbox
from sraf.tools import core_tool_registry, extended_tool_registry


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return run_command(args)
    if args.command == "chat":
        return chat_command(args)
    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sraf")
    subparsers = parser.add_subparsers(dest="command")
    run = subparsers.add_parser("run", help="Run the self-refining agent loop.")
    run.add_argument("query", help="User task.")
    add_runtime_args(run)

    chat = subparsers.add_parser("chat", help="Start an interactive multi-turn session.")
    chat.add_argument("query", nargs="?", help="Optional first user task.")
    add_runtime_args(chat)
    chat.add_argument(
        "--max-history-turns",
        type=int,
        default=6,
        help="How many previous turns to include in the next prompt.",
    )
    return parser


def add_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--base-prompt-file")
    parser.add_argument("--docker", action="store_true", help="Use Docker for run_python.")
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Disable GigaChat SSL certificate verification for local proxy setups.",
    )
    parser.add_argument(
        "--base-url",
        help="Custom GigaChat API base URL. Overrides GIGACHAT_BASE_URL env var.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use demo mode with mock LLM (no credentials needed).",
    )


def run_command(args: argparse.Namespace) -> int:
    loop, base_prompt = build_loop(args)
    result = loop.run(args.query, base_prompt=base_prompt)
    print(result.output or result.feedback)
    print(f"\nstatus={result.status}; attempts={result.attempts}", file=sys.stderr)
    return 0 if result.status == "success" else 2


def chat_command(args: argparse.Namespace) -> int:
    loop, base_prompt = build_loop(args)
    session = ConversationSession(
        loop,
        base_prompt=base_prompt,
        max_history_turns=args.max_history_turns,
    )

    print("SRAF chat. Введите задачу, :q или exit для выхода.")
    first_query = args.query
    while True:
        if first_query is not None:
            query = first_query
            first_query = None
            print(f"> {query}")
        else:
            try:
                query = input("> ").strip()
            except EOFError:
                print()
                return 0
        if query in {":q", ":quit", "exit", "quit"}:
            return 0
        if not query:
            continue
        result = session.ask(query)
        print(result.output or result.feedback)
        print(f"status={result.status}; attempts={result.attempts}", file=sys.stderr)


def build_loop(args: argparse.Namespace) -> tuple[MetaLoop, str]:
    # Initialize LLM client
    if args.demo:
        print("INFO: Using demo mode with mock LLM responses", file=sys.stderr)
        llm = ScriptedLLMClient([
            # Mock responses with valid JSON
            '["Respond with a greeting"]',  # instruction extraction
            '{"compliant": true, "explanation": "Demo mode"}',  # validation
            '{"final_answer": "Hello! This is a demo response.", "execution_log": []}',  # attempt result
            '{"status": "success", "feedback": "Task completed in demo mode."}',  # evaluation
            '["Demo response"]',  # refinement
        ])
    else:
        try:
            llm = GigaChatClient(
                verify_ssl_certs=False if args.no_verify_ssl else None,
                base_url=args.base_url,
            )
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            print("\nOptions:", file=sys.stderr)
            print("  1. Set GIGACHAT_CREDENTIALS environment variable", file=sys.stderr)
            print("  2. Use --demo flag for testing without credentials", file=sys.stderr)
            sys.exit(1)
    
    evaluator = Evaluator(llm)
    sandbox = DockerPythonSandbox() if args.docker else RestrictedSubprocessSandbox()
    core_tools = core_tool_registry(sandbox=sandbox, workspace_root=Path.cwd())
    ext_tools = extended_tool_registry(sandbox=sandbox, evaluator=evaluator, workspace_root=Path.cwd())
    runner = AgentRunner(llm, core_tools, ext_tools, max_steps=args.max_steps)
    base_prompt = BASE_SYSTEM_PROMPT
    if args.base_prompt_file:
        with open(args.base_prompt_file, encoding="utf-8") as stream:
            base_prompt = stream.read()

    loop = MetaLoop(
        extractor=InstructionExtractor(llm),
        runner=runner,
        evaluator=evaluator,
        refiner=PromptRefiner(llm),
        sanitizer=PromptSanitizer(llm),
        validator=InstructionValidator(llm),
        escalation=CliEscalation(),
        max_attempts=args.max_attempts,
    )
    return loop, base_prompt


if __name__ == "__main__":
    raise SystemExit(main())
