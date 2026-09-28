"""What an evidence item is. Facts are read from items and links tie items to projects, so both
modules name them the same way (docs/architecture/09)."""

from enum import StrEnum


class ItemKind(StrEnum):
    MESSAGE = "message"
    DOCUMENT = "document"
    INVOICE = "invoice"
    # A truck's reported position (logistics pack, shipment_locations).
    LOCATION = "location"
