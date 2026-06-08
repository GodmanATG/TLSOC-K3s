import os
import sys
import json
import redis
import time
import yaml
import logging
import traceback
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from kafka import KafkaConsumer
from core.schema import LogInput
from core.registry import RuleRegistry

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

# Load Config
try:
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    sys.exit(f"Config file not found: {CONFIG_PATH}")

OUTPUT_DIR = config["paths"]["output_dir"]
LOG_DIR = os.path.join(BASE_DIR, "logs")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Logging Setup
log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "engine.log"),
    maxBytes=10 * 1024 * 1024,
    backupCount=5
)
file_handler.setFormatter(log_formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

logger = logging.getLogger("soc-engine")
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Registry Setup
try:
    registry = RuleRegistry(
        rules_dir=os.path.join(BASE_DIR, config["paths"]["rules_dir"]),
        program_map=config.get("program_mapping", {})
    )
    logger.info("Rule registry initialized")
except Exception as e:
    logger.critical(f"Failed to initialize registry: {e}")
    sys.exit(1)

def write_dlq(raw_log, program, error):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "program": program,
        "error": str(error),
        "raw": raw_log,
    }
    try:
        with open(os.path.join(LOG_DIR, "dlq.json"), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.error(f"DLQ write failed: {e}")

class HealthMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.window_start = time.time()
        self.events_in_window = 0
        self.errors_in_window = 0
        self.total_events = 0
        self.rule_stats = {}

    def record_event(self):
        self.events_in_window += 1
        self.total_events += 1

    def record_error(self):
        self.errors_in_window += 1

    def _get_rule_stats(self, rule_name):
        if rule_name not in self.rule_stats:
            self.rule_stats[rule_name] = {
                "parsed_messages": 0,
                "parsed_events": 0,
                "no_match": 0,
                "buffered": 0,
                "errors": 0,
                "redis_errors": 0
            }
        return self.rule_stats[rule_name]

    def record_parsed(self, rule_name, event_count=1):
        stats = self._get_rule_stats(rule_name)
        stats["parsed_messages"] += 1
        stats["parsed_events"] += event_count

    def record_no_match(self, rule_name):
        self._get_rule_stats(rule_name)["no_match"] += 1

    def record_buffered(self, rule_name):
        self._get_rule_stats(rule_name)["buffered"] += 1

    def record_rule_error(self, rule_name):
        self._get_rule_stats(rule_name)["errors"] += 1

    def record_redis_error(self, rule_name):
        self._get_rule_stats(rule_name)["redis_errors"] += 1

    def flush_if_needed(self):
        now = time.time()
        if now - self.window_start < 60:
            return

        elapsed = now - self.window_start
        eps = self.events_in_window / elapsed if elapsed else 0

        stats = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_sec": int(now - self.start_time),
            "eps": round(eps, 2),
            "total_processed": self.total_events,
            "errors_last_min": self.errors_in_window,
            "parser_stats": self.rule_stats,
        }

        try:
            with open(os.path.join(LOG_DIR, "stats.json"), "w") as f:
                json.dump(stats, f)
            logger.info(f"Stats: {stats['eps']} EPS, {stats['total_processed']} processed, {stats['errors_last_min']} errors")
        except Exception:
            pass

        self.window_start = now
        self.events_in_window = 0
        self.errors_in_window = 0

monitor = HealthMonitor()

def flush_batch(batch):
    if not batch:
        return

    files = {}
    for event in batch:
        module = event.get("event", {}).get("module", "unknown")
        if isinstance(module, list):
            module = module[0] if module else "unknown"
        files.setdefault(str(module), []).append(json.dumps(event))

    for module, lines in files.items():
        try:
            with open(os.path.join(OUTPUT_DIR, f"{module}.json"), "a") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as e:
            logger.error(f"Batch write failed for {module}: {e}")

def main():
    try:
        consumer = KafkaConsumer(
            bootstrap_servers=config["kafka"]["bootstrap_servers"],
            group_id=config["kafka"]["group_id"],
            auto_offset_reset=config["kafka"]["auto_offset_reset"],
            enable_auto_commit=True,
            max_poll_records=2000,
            value_deserializer=lambda m: m.decode("utf-8", errors="ignore"),
        )
        consumer.subscribe(pattern=config["kafka"]["input_topic"])
        logger.info(f"Kafka consumer started on topic: {config['kafka']['input_topic']}")
    except Exception as e:
        logger.critical(f"Kafka connection error: {e}")
        sys.exit(1)

    batch = []
    last_flush = time.time()
    batch_size = config["batch"]["size"]
    batch_timeout = config["batch"]["timeout_sec"]

    try:
        while True:
            records = consumer.poll(timeout_ms=1000)

            for _, messages in records.items():
                for message in messages:
                    try:
                        log_input = LogInput(message.value)
                        if not log_input.valid:
                            write_dlq(message.value, "unknown", "invalid_envelope")
                            monitor.record_error()
                            continue

                        rule_name = registry.program_map.get(log_input.program) or log_input.program
                        processor = registry.get_processor(log_input.program)
                        if not processor:
                            write_dlq(message.value, log_input.program, "no_matching_rule")
                            monitor.record_error()
                            continue

                        try:
                            result = processor.process(log_input)
                            redis_error = getattr(processor, "last_redis_error", None)
                            if redis_error:
                                monitor.record_redis_error(rule_name)
                                logger.error(f"Redis error ({log_input.program}): {redis_error}")
                                if result is None:
                                    write_dlq(message.value, log_input.program, redis_error)
                                    monitor.record_error()
                                    monitor.record_rule_error(rule_name)
                                    continue
                            if result:
                                # Handle lists returned by XML parsing
                                if isinstance(result, list):
                                    batch.extend(result)
                                    for _ in result: monitor.record_event()
                                    monitor.record_parsed(rule_name, len(result))
                                else:
                                    batch.append(result)
                                    monitor.record_event()
                                    monitor.record_parsed(rule_name, 1)
                            else:
                                if processor.strategy == "stateful":
                                    if processor.id_regex and processor.id_regex.search(log_input.raw):
                                        monitor.record_buffered(rule_name)
                                    else:
                                        write_dlq(message.value, log_input.program, "no_match")
                                        monitor.record_error()
                                        monitor.record_rule_error(rule_name)
                                        monitor.record_no_match(rule_name)
                                else:
                                    write_dlq(message.value, log_input.program, "no_match")
                                    monitor.record_error()
                                    monitor.record_rule_error(rule_name)
                                    monitor.record_no_match(rule_name)
                        except Exception as e:
                            logger.error(f"Parsing error ({log_input.program}): {e}")
                            write_dlq(message.value, log_input.program, e)
                            monitor.record_error()
                            monitor.record_rule_error(rule_name)

                    except Exception as e:
                        logger.error(f"Envelope error: {e}")
                        write_dlq(message.value, "unknown", e)
                        monitor.record_error()

            now = time.time()
            if len(batch) >= batch_size or (batch and now - last_flush > batch_timeout):
                flush_batch(batch)
                batch.clear()
                last_flush = now

            monitor.flush_if_needed()

    except KeyboardInterrupt:
        logger.info("Stopping engine...")
        flush_batch(batch)
        consumer.close()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
