"""What the logistics pack plugs into the core: its document types, the facts its text holds,
how an accepted field proposal is written onto a shipment, which project cannot be merged into
another, a freight job's checklist, and the freight scenario a judge's sandbox opens with."""

from app.modules.documents import registry
from app.modules.facts import extractors
from app.modules.playground import service as playground
from app.modules.projects import checklist as checklists
from app.modules.projects import fields
from app.modules.projects import service as projects
from app.modules.projects.models import ProposalTarget
from app.packs.logistics import checklist, documents, facts, sandbox, service

for document_type in documents.TYPES:
    registry.register(document_type)
registry.register_classifier(documents.classify)
registry.register_classifier(documents.classify_by_name, by_name=True)

fields.register_target(ProposalTarget.SHIPMENT, service.accept_field)
projects.guard_merge(service.refuse_merge)
checklists.register(checklist.ITEMS, checklist.read)
playground.register_seed(sandbox.seed)

extractors.register("logistics.cmr_number", facts.cmr_numbers)
extractors.register("logistics.plate", facts.labelled_plates)
extractors.register("logistics.order_number", facts.order_numbers)
extractors.register_key_kind("cmr_number")
extractors.register_key_kind("order_number")
