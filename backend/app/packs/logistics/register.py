"""What the logistics pack plugs into the core: its document types, the facts its text holds, and
how an accepted field proposal is written onto a shipment."""

from app.modules.documents import registry
from app.modules.facts import extractors
from app.modules.projects import fields
from app.modules.projects.models import ProposalTarget
from app.packs.logistics import documents, facts, service

for document_type in documents.TYPES:
    registry.register(document_type)

fields.register_target(ProposalTarget.SHIPMENT, service.accept_field)

extractors.register("logistics.cmr_number", facts.cmr_numbers)
extractors.register("logistics.plate", facts.labelled_plates)
extractors.register("logistics.order_number", facts.order_numbers)
