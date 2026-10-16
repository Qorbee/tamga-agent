"""What the logistics pack plugs into the core: its document types, the facts its text holds,
how an accepted field proposal is written onto a shipment, which project cannot be merged into
another, a freight job's checklist, its trucks in the chat, what delivering means for paying a
bill, and the freight scenario a judge's sandbox opens with."""

from app.modules.assistant import news, tools
from app.modules.decisions import service as decisions
from app.modules.documents import registry
from app.modules.facts import extractors
from app.modules.playground import service as playground
from app.modules.projects import checklist as checklists
from app.modules.projects import fields
from app.modules.projects import service as projects
from app.modules.projects.models import ProposalTarget
from app.packs.logistics import assistant, checklist, documents, facts, sandbox, service

for document_type in documents.TYPES:
    registry.register(document_type)
registry.register_classifier(documents.classify)
registry.register_classifier(documents.classify_by_name, by_name=True)

fields.register_target(ProposalTarget.SHIPMENT, service.accept_field)
projects.guard_merge(service.refuse_merge)
checklists.register(checklist.ITEMS, checklist.read)
# Its trucks' tools are added to the assistant's when `assistant` is imported, above.
tools.name_trucks_with(assistant.name_trucks)
decisions.register_delivery(service.delivered)
for scenario in sandbox.SCENARIOS:
    playground.register_scenario(scenario)

extractors.register("logistics.cmr_number", facts.cmr_numbers)
extractors.register("logistics.plate", facts.labelled_plates)
extractors.register("logistics.order_number", facts.order_numbers)
extractors.register_key_kind("cmr_number")
extractors.register_key_kind("order_number")
# A truck the job has not had, named in a reply, may be the next load.
extractors.register_load_kind("plate")
news.register(assistant.positions)
