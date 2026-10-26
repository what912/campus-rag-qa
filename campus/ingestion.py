"""Keep bounded local import outcomes without storing failed file contents."""
import json
import threading
import uuid
from datetime import datetime,timezone
from pathlib import Path


class ImportLog:
    def __init__(self,directory):
        self.path=Path(directory)/'import-records.json'
        self.lock=threading.RLock()

    def list(self):
        with self.lock:
            return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else []

    def record(self,filename,status,**details):
        with self.lock:
            rows=self.list()
            rows.insert(0,{'id':uuid.uuid4().hex,'filename':Path(filename.replace('\\','/')).name[:180],
                          'status':status,'created_at':datetime.now(timezone.utc).isoformat(),**details})
            temporary=self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(rows[:200],ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.path)
