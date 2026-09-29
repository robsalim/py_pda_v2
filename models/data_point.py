from dataclasses import dataclass
from datetime import datetime

@dataclass
class DataPoint:
    timestamp: datetime
    value: float
