import json
import tempfile
from pathlib import Path


class ProjectFixture:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.data = self.base / "data"
        self.data.mkdir(parents=True)

    def write_json(self, name, payload):
        path = self.data / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def read_json(self, name):
        return json.loads((self.data / name).read_text(encoding="utf-8"))

    def close(self):
        self._tmp.cleanup()
