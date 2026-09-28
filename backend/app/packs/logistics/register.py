"""What the logistics pack plugs into the core: its document types, and how an accepted field
proposal is written onto a shipment."""

from app.modules.documents import registry
from app.modules.projects import fields
from app.modules.projects.models import ProposalTarget
from app.packs.logistics import documents, service

for document_type in documents.TYPES:
    registry.register(document_type)

fields.register_target(ProposalTarget.SHIPMENT, service.accept_field)
