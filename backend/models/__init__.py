"""
Model registry. Importing this package registers every ORM model on
Base.metadata, which is what create_all() uses to build the tables.
Add each new model module here as later phases create them.
"""

from backend.models.device import Device, DeviceStatus, DeviceType
from backend.models.water import WaterReading

__all__ = ["Device", "DeviceStatus", "DeviceType", "WaterReading"]