import os
import yaml
import time
import logging
from threading import Thread
from .engine import UniversalEngine

logger = logging.getLogger("soc-engine")

class RuleRegistry:
    def __init__(self, rules_dir, program_map):
        self.rules_dir = rules_dir
        self.program_map = program_map
        self.engines = {}
        self.reload()

        # Start Watcher
        self.watcher = Thread(target=self._watch_loop, daemon=True)
        self.watcher.start()

    def reload(self):
        new_engines = {}

        if not os.path.exists(self.rules_dir):
            logger.error(f"Rules directory not found: {self.rules_dir}")
            return

        for f in os.listdir(self.rules_dir):
            if f.endswith(".yaml"):
                try:
                    with open(os.path.join(self.rules_dir, f), 'r') as yml:
                        rule_config = yaml.safe_load(yml)

                        # Fallback: if 'pattern_name' missing, use filename
                        pattern_name = rule_config.get('pattern_name', f.replace('.yaml', ''))

                        new_engines[pattern_name] = UniversalEngine(rule_config)

                except Exception as e:
                    logger.error(f"Error loading rule {f}: {e}")

        self.engines = new_engines
        logger.info(f"Loaded {len(self.engines)} parsing rules.")

    def get_processor(self, source_program):
        # 1. Look up the Mapping
        pattern_name = self.program_map.get(source_program)

        if not pattern_name:
            # Fallback: If no map exists, try looking for a rule with the exact program name
            pattern_name = source_program

        return self.engines.get(pattern_name)

    def _watch_loop(self):
        last_mtime = 0
        while True:
            time.sleep(10)
            try:
                current_mtime = os.stat(self.rules_dir).st_mtime
                if current_mtime != last_mtime:
                    last_mtime = current_mtime
                    self.reload()
            except:
                pass
