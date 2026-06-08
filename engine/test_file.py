#!/usr/bin/env python3
import argparse
import json
import os
import sys
import yaml

from core.schema import LogInput
from core.registry import RuleRegistry

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")


def load_config():
    try:
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        sys.exit(f"Config file not found: {CONFIG_PATH}")


def build_log_input(raw, program, source_file):
    msg = json.dumps({
        "meta": {
            "source_program": program,
            "source_file": source_file
        },
        "raw": raw
    })
    return LogInput(msg)


def safe_process(engine, log_input):
    try:
        return engine.process(log_input), None
    except Exception as e:
        return None, str(e)


def resolve_engine_name(registry, engine_obj):
    for name, eng in registry.engines.items():
        if eng is engine_obj:
            return name
    return "unknown"


def add_sample(samples, reason, line_no, raw, limit):
    if limit <= 0:
        return
    if len(samples[reason]) < limit:
        samples[reason].append((line_no, raw))


def main():
    parser = argparse.ArgumentParser(
        description="Test a log file against a parser rule and summarize results."
    )
    parser.add_argument("file", help="Path to the input log file")
    parser.add_argument(
        "parser",
        help="Parser name, source program, or AUTO to try all rules"
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=5,
        help="How many sample lines to show per reason (default: 5)"
    )
    parser.add_argument(
        "--show-success",
        action="store_true",
        help="Dump matched events as JSON"
    )
    parser.add_argument(
        "--show-unparsed",
        action="store_true",
        help="Dump all unparsed lines with reason"
    )
    parser.add_argument(
        "--show-parsed",
        action="store_true",
        help="Dump all parsed lines with matched rule"
    )
    args = parser.parse_args()

    file_path = os.path.abspath(args.file)
    if not os.path.exists(file_path):
        sys.exit(f"Input file not found: {file_path}")

    config = load_config()
    registry = RuleRegistry(
        rules_dir=os.path.join(BASE_DIR, config["paths"]["rules_dir"]),
        program_map=config.get("program_mapping", {})
    )

    parser_name = args.parser
    auto_mode = parser_name.strip().upper() == "AUTO"

    if not auto_mode:
        engine = registry.engines.get(parser_name) or registry.get_processor(parser_name)
        if not engine:
            available = ", ".join(sorted(registry.engines.keys()))
            sys.exit(
                "Parser not found. Use AUTO or one of: "
                + (available if available else "(no rules loaded)")
            )
        resolved_name = resolve_engine_name(registry, engine)
    else:
        engine = None
        resolved_name = "AUTO"

    stats = {
        "lines_read": 0,
        "lines_nonempty": 0,
        "parsed_lines": 0,
        "parsed_events": 0,
        "no_match": 0,
        "buffered": 0,
        "errors": 0,
        "blank": 0
    }
    matched_by_engine = {}

    samples = {
        "no_match": [],
        "buffered": [],
        "errors": [],
        "blank": []
    }

    matched_events = [] if args.show_success else None
    unparsed_lines = [] if args.show_unparsed else None
    parsed_lines = [] if args.show_parsed else None

    with open(file_path, "r", errors="ignore") as f:
        for line_no, line in enumerate(f, start=1):
            stats["lines_read"] += 1
            raw = line.rstrip("\n")

            if not raw.strip():
                stats["blank"] += 1
                add_sample(samples, "blank", line_no, raw, args.samples)
                continue

            stats["lines_nonempty"] += 1

            if auto_mode:
                matched = False
                buffered = False
                error_seen = False

                for engine_name, engine_obj in registry.engines.items():
                    log_input = build_log_input(raw, engine_name, file_path)
                    result, err = safe_process(engine_obj, log_input)

                    if err:
                        error_seen = True
                        continue

                    if result:
                        matched = True
                        stats["parsed_lines"] += 1
                        if isinstance(result, list):
                            stats["parsed_events"] += len(result)
                            matched_by_engine[engine_name] = matched_by_engine.get(engine_name, 0) + len(result)
                            if matched_events is not None:
                                for event in result:
                                    matched_events.append((line_no, event))
                        else:
                            stats["parsed_events"] += 1
                            matched_by_engine[engine_name] = matched_by_engine.get(engine_name, 0) + 1
                            if matched_events is not None:
                                matched_events.append((line_no, result))
                        if parsed_lines is not None:
                            parsed_lines.append((line_no, engine_name, raw))
                        break

                    if engine_obj.strategy == "stateful" and hasattr(engine_obj, "id_regex"):
                        if engine_obj.id_regex.search(raw):
                            buffered = True

                if matched:
                    continue
                if error_seen:
                    stats["errors"] += 1
                    add_sample(samples, "errors", line_no, raw, args.samples)
                    if unparsed_lines is not None:
                        unparsed_lines.append((line_no, "errors", raw))
                elif buffered:
                    stats["buffered"] += 1
                    add_sample(samples, "buffered", line_no, raw, args.samples)
                    if unparsed_lines is not None:
                        unparsed_lines.append((line_no, "buffered", raw))
                else:
                    stats["no_match"] += 1
                    add_sample(samples, "no_match", line_no, raw, args.samples)
                    if unparsed_lines is not None:
                        unparsed_lines.append((line_no, "no_match", raw))

            else:
                log_input = build_log_input(raw, parser_name, file_path)
                result, err = safe_process(engine, log_input)

                if err:
                    stats["errors"] += 1
                    add_sample(samples, "errors", line_no, raw, args.samples)
                    if unparsed_lines is not None:
                        unparsed_lines.append((line_no, "errors", raw))
                    continue

                if result:
                    stats["parsed_lines"] += 1
                    if isinstance(result, list):
                        stats["parsed_events"] += len(result)
                        if matched_events is not None:
                            for event in result:
                                matched_events.append((line_no, event))
                    else:
                        stats["parsed_events"] += 1
                        if matched_events is not None:
                            matched_events.append((line_no, result))
                    if parsed_lines is not None:
                        parsed_lines.append((line_no, resolved_name, raw))
                    matched_by_engine[resolved_name] = matched_by_engine.get(resolved_name, 0) + 1
                    continue

                if engine.strategy == "stateful" and hasattr(engine, "id_regex"):
                    if engine.id_regex.search(raw):
                        stats["buffered"] += 1
                        add_sample(samples, "buffered", line_no, raw, args.samples)
                        if unparsed_lines is not None:
                            unparsed_lines.append((line_no, "buffered", raw))
                    else:
                        stats["no_match"] += 1
                        add_sample(samples, "no_match", line_no, raw, args.samples)
                        if unparsed_lines is not None:
                            unparsed_lines.append((line_no, "no_match", raw))
                else:
                    stats["no_match"] += 1
                    add_sample(samples, "no_match", line_no, raw, args.samples)
                    if unparsed_lines is not None:
                        unparsed_lines.append((line_no, "no_match", raw))

    unparsed = stats["no_match"] + stats["buffered"] + stats["errors"]

    print("\nFile Test Summary")
    print("-" * 60)
    print(f"File:           {file_path}")
    print(f"Parser:         {resolved_name}")
    print(f"Lines read:     {stats['lines_read']} (non-empty: {stats['lines_nonempty']}, blank: {stats['blank']})")
    print(f"Parsed lines:   {stats['parsed_lines']}")
    print(f"Parsed events:  {stats['parsed_events']}")
    print(f"Unparsed lines: {unparsed} (no_match: {stats['no_match']}, buffered: {stats['buffered']}, errors: {stats['errors']})")

    if matched_by_engine:
        print("\nParsed by rule")
        for name in sorted(matched_by_engine.keys()):
            print(f"- {name}: {matched_by_engine[name]}")

    def dump_samples(reason_label, lines):
        if not lines:
            return
        print(f"\nSample {reason_label} lines (up to {args.samples}):")
        for line_no, raw in lines:
            print(f"  {line_no}: {raw}")

    dump_samples("no_match", samples["no_match"])
    dump_samples("buffered", samples["buffered"])
    dump_samples("errors", samples["errors"])

    if matched_events is not None:
        print("\nMatched events")
        print("-" * 60)
        for line_no, event in matched_events:
            print(f"Line {line_no}:")
            print(json.dumps(event, indent=2))

    if unparsed_lines is not None:
        print("\nUnparsed lines")
        print("-" * 60)
        for line_no, reason, raw in unparsed_lines:
            print(f"{line_no} [{reason}]: {raw}")

    if parsed_lines is not None:
        print("\nParsed lines")
        print("-" * 60)
        for line_no, rule_name, raw in parsed_lines:
            print(f"{line_no} [{rule_name}]: {raw}")


if __name__ == "__main__":
    main()
