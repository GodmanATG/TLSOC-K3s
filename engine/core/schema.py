import json

class LogInput:
    def __init__(self, kafka_value):
        try:
            # Handle bytes or string input
            if isinstance(kafka_value, bytes):
                kafka_value = kafka_value.decode('utf-8')
            
            data = json.loads(kafka_value)
            self.meta = data.get("meta", {})
            self.raw = data.get("raw", "").strip()
            self.program = self.meta.get("source_program", "unknown")
            self.valid = True
        except Exception:
            self.valid = False
            self.raw = str(kafka_value)
            self.program = "unknown"
            self.meta = {}
