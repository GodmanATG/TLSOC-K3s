import json
import os
import sys
import yaml
import redis
from core.schema import LogInput
from core.registry import RuleRegistry

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.yaml')

try:
    with open(CONFIG_PATH, 'r') as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    sys.exit("Config file not found.")

print(f"Loading rules from {config['paths']['rules_dir']}...")
registry = RuleRegistry(
    rules_dir=os.path.join(BASE_DIR, config['paths']['rules_dir']),
    program_map=config.get('program_mapping', {})
)
print("Rules loaded.\n")

def get_user_choice():
    patterns = sorted(list(registry.engines.keys()))
    
    print("\nAvailable Parsers:")
    print(" 0. AUTO-DETECT (Try All)")
    
    for idx, name in enumerate(patterns):
        print(f" {idx + 1}. {name}")
    
    print("-" * 30)
    choice = input(f"Select Parser [0-{len(patterns)}] (or 'exit'): ").strip()
    
    if choice.lower() == 'exit':
        return None, None
    
    if choice == '0':
        return "AUTO", patterns
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(patterns):
            return patterns[idx], patterns
        else:
            print("Invalid selection.")
            return False, None
    except ValueError:
        return False, None

def test_single_engine(engine_name, raw_log):
    fake_msg = json.dumps({
        "meta": { "source_program": engine_name, "server": "test-box" },
        "raw": raw_log
    })
    log_input = LogInput(fake_msg)
    
    processor = registry.engines.get(engine_name)
    if not processor:
        return None

    try:
        event = processor.process(log_input)
        return event, processor.strategy
    except Exception:
        return None, "error"

def run_test():
    while True:
        try:
            selected_pattern, all_patterns = get_user_choice()
            if selected_pattern is None: break
            if selected_pattern is False: continue

            print("\nPaste Raw Log Line:")
            raw_log = input("Log > ").strip()
            if not raw_log: continue
            
            if raw_log.startswith('{\\"') or '\\"' in raw_log:
                print("Warning: Log appears to have escaped quotes. Ensure the input is clean.")

            print("-" * 50)

            if selected_pattern == "AUTO":
                print(f"Scanning {len(all_patterns)} parsers...")
                found = False
                
                for pattern in all_patterns:
                    event, strategy = test_single_engine(pattern, raw_log)
                    if event:
                        print(f"MATCH FOUND using parser: [{pattern}]")
                        print(f"Strategy: {strategy}")
                        print(json.dumps(event, indent=2))
                        found = True
                        break
                
                if not found:
                     print("No parser could interpret this log.")

            else:
                print(f"Testing against: [{selected_pattern}]")
                event, strategy = test_single_engine(selected_pattern, raw_log)
                
                if event:
                    print("\nPARSING SUCCESS:")
                    print(json.dumps(event, indent=2))
                else:
                    if strategy == 'stateful':
                        print("\nBUFFERED (Stateful):")
                        print("Log added to Redis. Waiting for end_signal.")
                    elif strategy == 'json_map':
                        print("\nJSON PARSE FAILED:")
                        print("Input is not valid JSON.")
                    else:
                        print("\nREGEX FAILED:")
                        print("Log did not match any regex patterns.")

        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    try:
        r = redis.Redis(host='localhost', port=6379, db=0)
        r.ping()
    except:
        print("Warning: Redis is not running. Stateful tests will fail.")
    
    run_test()
